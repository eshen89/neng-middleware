"""neng-middleware — shared verification package for the Neng services (not a deployed service).

Two responsibilities, deliberately tiny:
  - `current_user`: verify a Supabase JWT (asymmetric JWKS, fail-closed) and return the `sub`,
    with an offline bypass for tests/eval and a prod-boot guard against shipping that bypass.
  - `signed_headers`: SigV4-sign an internal service-to-service request (D8).

Consumed by neng-agent and neng-user via a pinned git tag (editable path dep for local dev).
"""

from __future__ import annotations

from .auth import current_user, reset_jwks_cache, verify_token
from .settings import DEV_USER, AuthSettings, assert_safe_boot
from .settings import load as load_auth_settings
from .sigv4 import signed_headers

__all__ = [
    "DEV_USER",
    "AuthSettings",
    "assert_safe_boot",
    "current_user",
    "load_auth_settings",
    "reset_jwks_cache",
    "signed_headers",
    "verify_token",
]
