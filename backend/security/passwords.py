"""Versioned scrypt password hashes, using Python/OpenSSL with no weaker fallback."""
import base64
import binascii
import hashlib
import hmac
import secrets

N, R, P, LENGTH = 2**17, 8, 1, 32
MAXMEM = 256 * 1024 * 1024


def _encode(value):
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value):
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def _derive(password, salt):
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=N, r=R, p=P, dklen=LENGTH, maxmem=MAXMEM)


def valid_password_input(password):
    if not isinstance(password, str) or not 1 <= len(password) <= 1024:
        return False
    try:
        return len(password.encode("utf-8")) <= 4096
    except UnicodeError:
        return False


def hash_password(password):
    if not valid_password_input(password) or len(password) < 12:
        raise ValueError("Password must contain 12 to 1024 characters and at most 4096 UTF-8 bytes")
    salt = secrets.token_bytes(16)
    derived = _derive(password, salt)
    return f"scrypt$v1${N}${R}${P}${_encode(salt)}${_encode(derived)}"


def verify_password(password, encoded):
    if not valid_password_input(password) or not isinstance(encoded, str) or len(encoded) > 256:
        return False
    try:
        algorithm, version, n, r, p, salt, expected = encoded.split("$")
        if (algorithm, version, n, r, p) != ("scrypt", "v1", str(N), str(R), str(P)):
            return False
        salt, expected = _decode(salt), _decode(expected)
        if len(salt) != 16 or len(expected) != LENGTH:
            return False
    except (ValueError, binascii.Error):
        return False
    return hmac.compare_digest(_derive(password, salt), expected)
