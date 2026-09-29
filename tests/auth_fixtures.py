"""TEST ONLY: process-local auth repository. Never a production persistence adapter."""
from dataclasses import replace
from threading import Lock


class InMemoryAuthRepository:
    is_fixture = True

    def __init__(self, users=()):
        self.users = {user.user_id: user for user in users}
        self.sessions = {}
        self.attempts = {}
        self.lock = Lock()

    def get_user_by_username(self, username):
        return next((user for user in self.users.values() if user.username == username), None)

    def create_user(self, user):
        with self.lock:
            if any(existing.username == user.username for existing in self.users.values()):
                return False
            self.users[user.user_id] = user
            return True

    def get_user_by_id(self, user_id):
        return self.users.get(user_id)

    def create_session(self, session):
        with self.lock:
            if session.token_digest in self.sessions:
                raise RuntimeError("Session collision")
            self.sessions[session.token_digest] = session

    def get_session(self, token_digest):
        return self.sessions.get(token_digest)

    def revoke_session(self, token_digest):
        with self.lock:
            self.sessions[token_digest] = replace(self.sessions[token_digest], revoked=True)

    def consume_login_attempt(self, key, now, window_seconds, limit):
        with self.lock:
            bucket = (key, now // window_seconds)
            self.attempts[bucket] = self.attempts.get(bucket, 0)+1
            return self.attempts[bucket] <= limit
