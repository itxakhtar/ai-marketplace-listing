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
from app.services.description_service import DescriptionService
from app.services.image_service import ImageService
from app.services.listing_service import ListingService
from app.services.related_images_service import RelatedImagesService
from app.services.vision_service import ResilientVision, VisionService

logger = logging.getLogger(__name__)

DESCRIPTION = """
Generate structured marketplace listings (title, description, category, condition,
features, tags) from product photos using a Vision LLM.

* Images are validated and normalized with Pillow before inference.
* Output is validated against a strict schema; malformed AI output is never returned.
* Condition and features are based only on visible evidence.
"""


_DOC_PATHS = {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}


def _apply_docs_policy(app: FastAPI, settings: Settings | None) -> None:
    """Remove the interactive docs routes when ENABLE_DOCS=false."""
    if settings is not None and not settings.enable_docs:
        app.router.routes = [r for r in app.router.routes if getattr(r, "path", None) not in _DOC_PATHS]


def _build_services(settings: Settings) -> dict:
    """One shared Gemini client powers vision, text and image generation."""
    gemini = VisionService(settings)
    images = ImageService(settings)
    return {
        "listing_service": ListingService(images, ResilientVision(gemini, settings), settings),
        "description_service": DescriptionService(gemini, settings),
        "related_images_service": RelatedImagesService(images, gemini, settings),
    }


def create_app(
    settings: Settings | None = None,
    listing_service: ListingService | None = None,
    description_service: DescriptionService | None = None,
    related_images_service: RelatedImagesService | None = None,
) -> FastAPI:
    """Both arguments are injection points for tests; production passes neither."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Fail fast at startup if GEMINI_API_KEY etc. are missing.
        if app.state.settings is None:
            app.state.settings = get_settings()
        configure_logging(app.state.settings.log_level)
        _apply_docs_policy(app, app.state.settings)
        built = None
        for name in ("listing_service", "description_service", "related_images_service"):
            if getattr(app.state, name) is None:
                built = built or _build_services(app.state.settings)
                setattr(app.state, name, built[name])
        logger.info("app_started", extra={"model": app.state.settings.gemini_model})
        yield

    app = FastAPI(
        title="AI Marketplace Listing Generator",
        version="1.1.0",
        description=DESCRIPTION,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.listing_service = listing_service
    app.state.description_service = description_service
    app.state.related_images_service = related_images_service
    _apply_docs_policy(app, settings)

    install_request_context(app)
    register_exception_handlers(app)
    app.include_router(router)
    return app


app = create_app()
