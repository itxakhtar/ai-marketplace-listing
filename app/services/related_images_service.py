"""Generates a few AI "related" pictures from the user's product photo(s).

Honesty rules baked in: every generated image carries a visible "AI-generated" label and
the response carries a notice. Individual failures are tolerated (partial results are
returned); only when every image fails does the call fail.
"""

import asyncio
import base64
import io
import logging
from collections.abc import Sequence

from PIL import Image, ImageDraw, ImageFont

from app.core.config import Settings
from app.core.exceptions import AppError, InvalidImageError, VisionServiceError
from app.models.extras import RelatedImage, RelatedImagesResponse
from app.models.image import ImagePayload, UploadedImage
from app.services.image_service import ImageService
from app.services.vision_service import ImageGenProvider

logger = logging.getLogger(__name__)

MAX_REFERENCES = 3
LABEL_TEXT = "AI-generated"

# (label, scene instruction)
VARIANTS: tuple[tuple[str, str], ...] = (
    ("Studio view", "a clean studio product shot on a plain light background with a soft shadow, item centred"),
    ("Alternate angle", "the same item seen from a different, natural three-quarter angle on a neutral background"),
    ("In context", "a tasteful, realistic everyday setting where the item is clearly the main subject"),
)


def build_image_prompt(scene: str, item_name: str | None) -> str:
    name = f' (a "{item_name}")' if item_name else ""
    return (
        f"Using the reference photo(s) of one product{name}, create a new realistic product image: {scene}.\n"
        "The item must be the SAME product: same shape, proportions, colour, material and any visible markings.\n"
        "Do NOT add text, captions, watermarks, logos, price tags, people, extra accessories, "
        "or features that are not visible in the reference. Do not change the product.\n"
        "Return only the image."
    )


class RelatedImagesService:
    def __init__(self, image_service: ImageService, generator: ImageGenProvider, settings: Settings) -> None:
        self._images = image_service
        self._generator = generator
        self._settings = settings
        self._slots = asyncio.Semaphore(settings.vision_max_concurrency)

    async def generate(
        self, uploads: Sequence[UploadedImage], item_name: str | None = None
    ) -> RelatedImagesResponse:
        references = await self._prepare(uploads)
        variants = VARIANTS[: self._settings.related_image_count]
        name = (item_name or "").strip()[:120] or None

        results = await asyncio.gather(
            *(self._one(references, label, scene, name) for label, scene in variants),
            return_exceptions=True,
        )
        images = [r for r in results if isinstance(r, RelatedImage)]
        errors = [r for r in results if isinstance(r, BaseException)]
        logger.info("related_images_done", extra={"requested": len(variants), "succeeded": len(images)})

        if not images:
            first = next((e for e in errors if isinstance(e, AppError)), None)
            raise first or VisionServiceError()
        return RelatedImagesResponse(images=images)

    # ---------------------------------------------------------------- internals
    async def _prepare(self, uploads: Sequence[UploadedImage]) -> list[ImagePayload]:
        if not uploads:
            raise InvalidImageError("At least one image is required.")
        limit = self._settings.max_images_per_request
        if len(uploads) > limit:
            raise InvalidImageError(f"Too many images: {len(uploads)} uploaded, maximum is {limit}.")
        payloads = [await asyncio.to_thread(self._images.process, u) for u in uploads[:MAX_REFERENCES]]
        return payloads

    async def _one(
        self, references: list[ImagePayload], label: str, scene: str, item_name: str | None
    ) -> RelatedImage:
        async with self._slots:
            raw = await self._generator.generate_image(references, build_image_prompt(scene, item_name))
        jpeg = await asyncio.to_thread(self._finalize, raw)
        return RelatedImage(label=label, data_base64=base64.b64encode(jpeg).decode("ascii"))

    def _finalize(self, raw: bytes) -> bytes:
        """Decode, bound the size, burn in the AI label, re-encode as JPEG."""
        try:
            with Image.open(io.BytesIO(raw)) as src:
                src.load()
                image = src.convert("RGB")
        except Exception as exc:  # noqa: BLE001 - any decode problem = unusable output
            logger.warning("generated_image_unreadable", extra={"error_type": type(exc).__name__})
            raise VisionServiceError() from exc

        edge = self._settings.generated_image_max_edge
        image.thumbnail((edge, edge), Image.Resampling.LANCZOS)
        if self._settings.watermark_generated_images:
            image = _stamp(image)
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=84, optimize=True)
        return out.getvalue()


def _stamp(image: Image.Image) -> Image.Image:
    size = max(14, image.width // 20)
    try:
        font = ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow
        font = ImageFont.load_default()
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    pad = max(6, size // 2)
    left, top, right, bottom = draw.textbbox((0, 0), LABEL_TEXT, font=font)
    w, h = right - left, bottom - top
    x0, y1 = pad, image.height - pad
    box = (x0, y1 - h - pad, x0 + w + 2 * pad, y1)
    draw.rounded_rectangle(box, radius=pad, fill=(15, 18, 38, 190))
    draw.text((x0 + pad - left, y1 - h - pad // 2 - top), LABEL_TEXT, font=font, fill=(255, 255, 255, 255))
    return Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
