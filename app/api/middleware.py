"""Request-ID correlation, access logging, and last-resort 500 handling."""

import logging
import re
import time
import uuid

from fastapi import FastAPI, Request

from app.api.errors import error_response
from app.core.exceptions import AppError
from app.core.logging import request_id_ctx

logger = logging.getLogger(__name__)

# Only accept short, harmless client-supplied IDs (prevents log injection).
_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")


def _declared_body_too_large(request: Request) -> int:
    """Return the declared Content-Length if it exceeds the limit, else 0.

    Cheap early rejection. Chunked uploads without Content-Length are still capped
    per file by the route; put a body-size limit on your reverse proxy as well.
    """
    settings = getattr(request.app.state, "settings", None)
    declared = request.headers.get("content-length", "")
    if settings is None or not declared.isdigit():
        return 0
    size = int(declared)
    return size if size > settings.max_request_bytes else 0


def install_request_context(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_context(request: Request, call_next):
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if _REQUEST_ID_RE.fullmatch(incoming) else uuid.uuid4().hex
        request.state.request_id = request_id
        token = request_id_ctx.set(request_id)
        started = time.perf_counter()
        try:
            try:
                too_big = _declared_body_too_large(request)
                if too_big:
                    logger.info("request_too_large", extra={"content_length": too_big})
                    response = error_response(
                        413, "request_too_large", "The request body is too large.", request_id
                    )
                else:
                    response = await call_next(request)
            except Exception as exc:  # noqa: BLE001 - never leak internals to clients
                # Log only the type: messages/tracebacks can contain sensitive data.
                logger.error("unhandled_error", extra={"error_type": type(exc).__name__})
                response = error_response(
                    500, "internal_error", AppError.public_message, request_id
                )
            response.headers["X-Request-ID"] = request_id
            logger.info(
                "request_completed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000),
                },
            )
            return response
        finally:
            request_id_ctx.reset(token)
