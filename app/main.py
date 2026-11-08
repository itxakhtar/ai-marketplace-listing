"""FastAPI application factory.

Run:  uvicorn app.main:app --reload
Docs: /docs (Swagger UI) and /redoc
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.errors import register_exception_handlers
from app.api.middleware import install_request_context
from app.api.routes import router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.services.image_service import ImageService
from app.services.listing_service import ListingService
from app.services.vision_service import ResilientVision, VisionService

logger = logging.getLogger(__name__)

DESCRIPTION = """
Generate structured marketplace listings (title, description, category, condition,
features, tags) from product photos using a Vision LLM.

* Images are validated and normalized with Pillow before inference.
* Output is validated against a strict schema; malformed AI output is never returned.
* Condition and features are based only on visible evidence.
"""


def _build_listing_service(settings: Settings) -> ListingService:
    vision = ResilientVision(VisionService(settings), settings)
    return ListingService(ImageService(settings), vision, settings)


def create_app(
    settings: Settings | None = None,
    listing_service: ListingService | None = None,
) -> FastAPI:
    """Both arguments are injection points for tests; production passes neither."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Fail fast at startup if GEMINI_API_KEY etc. are missing.
        if app.state.settings is None:
            app.state.settings = get_settings()
        configure_logging(app.state.settings.log_level)
        if app.state.listing_service is None:
            app.state.listing_service = _build_listing_service(app.state.settings)
        logger.info("app_started", extra={"model": app.state.settings.gemini_model})
        yield

    app = FastAPI(
        title="AI Marketplace Listing Generator",
        version="1.0.0",
        description=DESCRIPTION,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.listing_service = listing_service

    install_request_context(app)
    register_exception_handlers(app)
    app.include_router(router)
    return app


app = create_app()
