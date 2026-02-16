"""Why this exists: Provides shared dependencies for FastAPI routes.
What it does: Implements X-Admin-Key security check for administrative endpoints.
"""

from __future__ import annotations

import logfire
from fastapi import Header, HTTPException, status

from core.config import settings


async def verify_admin_key(x_admin_key: str = Header(None)):
    """
    Why this exists: Secures sensitive administrative endpoints.
    What it does: Compares the provided X-Admin-Key header with the secret.
    """
    if not x_admin_key or x_admin_key != settings.X_ADMIN_KEY:
        logfire.warn(
            "Unauthorized admin access attempt with key: {key}", key=x_admin_key
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Admin-Key",
        )
