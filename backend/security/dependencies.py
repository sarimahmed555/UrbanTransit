"""FastAPI dependencies sharing the same service and policy as the dispatcher."""
import ipaddress

from .contracts import Permission, SecurityError
from .rbac import require_permission


def make_dependencies(auth_service, *, demo_mode=False):
    try:
        from fastapi import Depends, Request
    except ImportError as exc:
        raise RuntimeError("FastAPI runtime is NOT_CONFIGURED; dependency is not installed") from exc

    def current_user(request: Request):
        return auth_service.authenticate(request.headers.get("authorization"))

    def demo_user(request: Request):
        if not demo_mode:
            return current_user(request)
        host = request.client.host if request.client else ""
        try:
            is_loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            is_loopback = False
        if not is_loopback:
            raise SecurityError(403, "forbidden", "Access denied")
        return None

    def require_permissions(*permissions):
        demo_permissions = {
            Permission.ANALYTICS_READ,
            Permission.DIAGNOSTICS_READ,
            Permission.SCENARIOS_EXECUTE,
        }
        if demo_mode and all(permission in demo_permissions for permission in permissions):
            return demo_user

        def authorize(principal=Depends(demo_user)):
            for permission in permissions:
                require_permission(principal, permission)
            return principal
        return authorize

    return current_user, require_permissions, demo_user
