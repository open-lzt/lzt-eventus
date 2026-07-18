"""Self-hosted Scalar API reference — reads this app's own OpenAPI spec.

Scalar's renderer JS loads from a CDN at request time (same as FastAPI's built-in
Swagger UI already does for `/docs`), but the page and the OpenAPI spec it points
at are both served by this engine — no scalar.com account, no external doc host.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["docs"])

_PAGE = """<!doctype html>
<html>
  <head>
    <title>lzt-core management API</title>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
  </head>
  <body>
    <script id="api-reference" data-url="{openapi_url}"></script>
    <script src="https://cdn.jsdelivr.net/npm/@scalar/api-reference"></script>
  </body>
</html>
"""


@router.get("/scalar", include_in_schema=False)
async def scalar_docs(request: Request) -> HTMLResponse:
    openapi_url = request.app.openapi_url or "/openapi.json"
    return HTMLResponse(_PAGE.format(openapi_url=openapi_url))
