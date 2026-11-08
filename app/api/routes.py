"""HTTP layer only: parse multipart, delegate to ListingService, return JSON.

No provider (Gemini) logic lives here.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile

from app.api.dependencies import get_app_settings, get_listing_service
from app.api.schemas import ErrorResponse, HealthResponse
from app.core.config import Settings
from app.core.exceptions import ImageTooLargeError, InvalidImageError
from app.models.image import UploadedImage
from app.models.listing import ListingResponse
from app.services.listing_service import ListingService

logger = logging.getLogger(__name__)

router = APIRouter()


async def _read_limited(upload: UploadFile, index: int, max_bytes: int, max_mb: int) -> UploadedImage:
    """Read at most max_bytes+1 so an oversized upload is never fully buffered."""
    data = await upload.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ImageTooLargeError(f"Image {index}: exceeds the {max_mb} MB size limit.")
    return UploadedImage(data=data, filename=upload.filename, content_type=upload.content_type)


@router.post(
    "/api/v1/generate-listing/",
    response_model=ListingResponse,
    tags=["Listings"],
    summary="Generate a marketplace listing from product images",
    description=(
        "Upload one or more JPG/PNG/WEBP images as `multipart/form-data` using the repeated "
        "field name **`images`**. All images are treated as different views of the **same** "
        "product and produce **one** listing.\n\n"
        "Category and condition come from fixed taxonomies. Condition and features are based "
        "only on what is visible; the service never invents specifications, brands, "
        "functionality, or warranty."
    ),
    responses={
        400: {"model": ErrorResponse, "description": "Invalid or missing image / bad request"},
        413: {"model": ErrorResponse, "description": "Image exceeds the size limit"},
        422: {"model": ErrorResponse, "description": "No product found, or AI output failed validation"},
        429: {"model": ErrorResponse, "description": "Vision provider rate limit (see Retry-After)"},
        500: {"model": ErrorResponse, "description": "Unexpected server error"},
        503: {"model": ErrorResponse, "description": "Vision provider unavailable, timed out, or misconfigured"},
    },
)
async def generate_listing(
    images: Annotated[
        list[UploadFile],
        File(description="One or more product images (JPG, PNG, WEBP). Max 10 MB each."),
    ],
    service: Annotated[ListingService, Depends(get_listing_service)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> ListingResponse:
    if len(images) > settings.max_images_per_request:
        raise InvalidImageError(
            f"Too many images: {len(images)} uploaded, maximum is {settings.max_images_per_request}."
        )
    uploads = [
        await _read_limited(f, i, settings.max_image_size_bytes, settings.max_image_size_mb)
        for i, f in enumerate(images, start=1)
    ]
    return await service.generate(uploads)


@router.get("/health", response_model=HealthResponse, tags=["System"], summary="Liveness check")
async def health() -> HealthResponse:
    return HealthResponse()
