"""Authentication dependencies for admin API endpoints.

Two guards:
  - require_admin_token()  — HTTP endpoints (Bearer token in Authorization header)
  - require_admin_ws()     — WebSocket endpoints (?token= query param)

Behavior matrix:
  ADMIN_API_KEY set + valid token   → allow
  ADMIN_API_KEY set + wrong/missing → 401 Unauthorized
  ADMIN_API_KEY empty + DEBUG=true  → allow (local dev convenience)
  ADMIN_API_KEY empty + DEBUG=false → 403 Forbidden (locked in production)
"""

from __future__ import annotations

import logging

from fastapi import Depends, HTTPException, Query, WebSocket, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from scheduling.config import settings

log = logging.getLogger("scheduling.auth")

_bearer_scheme = HTTPBearer(auto_error=False)


async def require_admin_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> None:
    """FastAPI dependency — protect HTTP admin endpoints with bearer token."""
    key = settings.admin_api_key

    if not key:
        # No key configured
        if settings.debug:
            return  # Local dev — allow without auth
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin API key not configured. Set ADMIN_API_KEY in .env.",
        )

    if credentials is None or credentials.credentials != key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing admin token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def require_admin_ws(
    websocket: WebSocket,
    token: str = Query(default=""),
) -> None:
    """WebSocket auth — browsers can't send headers, so use ?token= query param."""
    key = settings.admin_api_key

    if not key:
        if settings.debug:
            return
        await websocket.close(code=4003, reason="Admin API key not configured")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    if token != key:
        await websocket.close(code=4001, reason="Unauthorized")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
