"""Single-screen Quick Test UI route.

Why this exists: Expose a web testing workspace combining Admin HITL Zone and Chat Zone
so developers can test agent flows without Telegram integration.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["ui"])

STATIC_DIR = Path(__file__).parent.parent / "static"
INDEX_HTML_PATH = STATIC_DIR / "index.html"


@router.get("/ui", response_class=HTMLResponse)
async def get_test_ui():
    """Serves the Single-Screen Quick Test UI."""
    if not INDEX_HTML_PATH.exists():
        return HTMLResponse(
            content="<h1>Test UI page not found</h1>",
            status_code=404,
        )
    return HTMLResponse(content=INDEX_HTML_PATH.read_text(encoding="utf-8"))
