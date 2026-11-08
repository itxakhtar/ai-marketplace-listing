"""Orchestrates: uploads -> ImageService -> VisionProvider -> parse -> validate.

This is the application layer. It knows nothing about FastAPI or Gemini.
"""

import asyncio
import json
import logging
import re
import time
from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError

from app.core.config import Settings
from app.core.exceptions import (
    ImageTooLargeError,
    InvalidImageError,
    ListingValidationError,
    NoProductDetectedError,
    RequestTimeoutError,
)
from app.models.enums import Category, Condition
from app.models.image import ImagePayload, UploadedImage
from app.models.listing import ListingResponse
from app.prompts.listing_prompt import NO_PRODUCT_ERROR, build_listing_prompt
from app.services.guardrails import GuardrailViolation, check_guardrails, retry_feedback
from app.services.image_service import ImageService
from app.services.vision_service import VisionProvider

logger = logging.getLogger(__name__)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)

def parse_llm_json(raw: str) -> dict[str, Any]:
    """Safely extract a JSON object from model text (handles fences / chatter)."""
    text = _FENCE.sub("", raw.strip()).strip()
    candidates = [text]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ListingValidationError()


def _drop_non_search_tags(data: dict[str, Any]) -> dict[str, Any]:
    """Remove tags that merely repeat a category or condition label."""
    tags = data.get("tags")
    if not isinstance(tags, list):
        return data
    banned = {c.value.lower() for c in Category} | {c.value.lower() for c in Condition}
    kept = [t for t in tags if not (isinstance(t, str) and t.strip().lstrip("#").lower() in banned)]
    return {**data, "tags": kept}


class ListingService:
    def __init__(
        self,
        image_service: ImageService,
        vision: VisionProvider,
        settings: Settings,
    ) -> None:
        self._images = image_service
        self._vision = vision
        self._settings = settings

    async def generate(self, uploads: Sequence[UploadedImage]) -> ListingResponse:
        """Build a listing, bounded by the overall ``request_timeout`` deadline."""
        try:
            return await asyncio.wait_for(
                self._generate(uploads), timeout=self._settings.request_timeout
            )
        except asyncio.TimeoutError as exc:
            logger.warning("request_deadline_exceeded")
            raise RequestTimeoutError() from exc

    async def _generate(self, uploads: Sequence[UploadedImage]) -> ListingResponse:
        started = time.perf_counter()
        payloads = await self._prepare_images(uploads)
        notes = self._quality_notes(payloads)
        prompt = build_listing_prompt(len(payloads), notes)

        listing: ListingResponse | None = None
        attempts = self._settings.max_ai_attempts
        for attempt in range(1, attempts + 1):
            raw = await self._vision.analyze(payloads, prompt)  # vision errors propagate
            try:
                listing = self.parse_and_validate(raw)
                break
            except NoProductDetectedError:
                raise  # a definitive answer, never retried
            except (ListingValidationError, GuardrailViolation) as exc:
                logger.warning(
                    "ai_output_rejected",
                    extra={"attempt": attempt, "attempts": attempts, "reason": self._reason(exc)},
                )
                if attempt == attempts:
                    raise ListingValidationError() from exc
                feedback = retry_feedback(exc.rules) if isinstance(exc, GuardrailViolation) else ""
                prompt = build_listing_prompt(len(payloads), notes, feedback or None)

        assert listing is not None
        listing = self._apply_quality_downgrade(listing, payloads)
        logger.info(
            "listing_generated",
            extra={
                "image_count": len(payloads),
                "attempts_used": attempt,
                "duration_ms": round((time.perf_counter() - started) * 1000),
                "category": listing.category.value,
                "condition": listing.condition_estimate.value,
                "poor_quality_images": sum(1 for p in payloads if p.quality and p.quality.is_poor),
            },
        )
        return listing

    # ---------------------------------------------------------------- internals
    async def _prepare_images(self, uploads: Sequence[UploadedImage]) -> list[ImagePayload]:
        if not uploads:
            raise InvalidImageError("At least one image is required.")
        limit = self._settings.max_images_per_request
        if len(uploads) > limit:
            raise InvalidImageError(f"Too many images: {len(uploads)} uploaded, maximum is {limit}.")

        payloads: list[ImagePayload] = []
        for index, upload in enumerate(uploads, start=1):
            try:
                # Pillow work is CPU-bound; keep it off the event loop.
                payloads.append(await asyncio.to_thread(self._images.process, upload))
            except (InvalidImageError, ImageTooLargeError) as exc:
                logger.info("image_rejected", extra={"image_index": index, "reason": str(exc)})
                raise type(exc)(f"Image {index}: {exc.public_message}") from exc
        return payloads

    @staticmethod
    def _quality_notes(payloads: Sequence[ImagePayload]) -> list[str]:
        notes = []
        for index, payload in enumerate(payloads, start=1):
            if payload.quality and payload.quality.is_poor:
                notes.append(f"Image {index} appears {' and '.join(payload.quality.problems)}.")
        return notes

    @staticmethod
    def _apply_quality_downgrade(
        listing: ListingResponse, payloads: Sequence[ImagePayload]
    ) -> ListingResponse:
        """If EVERY image is poor, condition cannot be trusted: say so explicitly."""
        all_poor = bool(payloads) and all(p.quality and p.quality.is_poor for p in payloads)
        if all_poor and listing.condition_estimate is not Condition.UNABLE_TO_DETERMINE:
            logger.info(
                "condition_downgraded",
                extra={"from_condition": listing.condition_estimate.value},
            )
            return listing.model_copy(update={"condition_estimate": Condition.UNABLE_TO_DETERMINE})
        return listing

    def parse_and_validate(self, raw: str) -> ListingResponse:
        data = parse_llm_json(raw)
        if data.get("error") == NO_PRODUCT_ERROR:
            raise NoProductDetectedError()
        try:
            listing = ListingResponse.model_validate(_drop_non_search_tags(data))
        except ValidationError as exc:
            fields = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})
            raise ListingValidationError(f"Invalid fields: {', '.join(fields)}") from exc
        check_guardrails(listing, block_unverified_specs=self._settings.block_unverified_specs)
        return listing

    @staticmethod
    def _reason(exc: Exception) -> str:
        return f"guardrail:{exc}" if isinstance(exc, GuardrailViolation) else str(exc)
