"""Gemini Vision integration. The ONLY module that imports the Google SDK.

Contract: image payloads + prompt in, raw model text out. Parsing and schema
validation belong to ListingService. Provider exceptions are translated into
domain exceptions and never leak to callers.

Error classification is deliberately independent of the HTTP library the SDK
uses internally (httpx, httpx2, ...): timeouts are enforced with asyncio and
unknown exceptions are classified by class-name, not by import.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from app.core.config import Settings
from app.core.exceptions import (
    VisionConfigurationError,
    VisionEmptyResponseError,
    VisionRateLimitError,
    VisionServiceError,
    VisionTimeoutError,
    VisionUnavailableError,
)
from app.models.image import ImagePayload

logger = logging.getLogger(__name__)


class VisionProvider(Protocol):
    """What ListingService needs. Any vision backend can implement this."""

    async def analyze(self, images: Sequence[ImagePayload], prompt: str) -> str: ...


class TextProvider(Protocol):
    """Text-only completion (JSON out). Used for description expansion."""

    async def complete(self, prompt: str) -> str: ...


class ImageGenProvider(Protocol):
    """Image generation from reference photos. Returns the raw encoded image bytes."""

    async def generate_image(self, references: Sequence[ImagePayload], prompt: str) -> bytes: ...


class VisionService:
    # Hard asyncio deadline = api_timeout + grace; guarantees we never hang
    # even if the SDK's own timeout does not fire.
    _timeout_grace_s: float = 5.0

    def __init__(self, settings: Settings, client: genai.Client | None = None) -> None:
        self._settings = settings
        # The SDK timeout is expressed in milliseconds.
        self._client = client or genai.Client(
            api_key=settings.gemini_api_key.get_secret_value(),
            http_options=types.HttpOptions(timeout=int(settings.api_timeout * 1000)),
        )

    async def analyze(self, images: Sequence[ImagePayload], prompt: str) -> str:
        """Send images + prompt to Gemini and return the raw text response."""
        if not images:
            raise ValueError("At least one image is required.")

        parts: list[types.Part | str] = [
            types.Part.from_bytes(data=img.data, mime_type=img.mime_type) for img in images
        ]
        parts.append(prompt)

        config = types.GenerateContentConfig(
            temperature=self._settings.gemini_temperature,
            max_output_tokens=self._settings.gemini_max_output_tokens,
            response_mime_type="application/json",
        )

        started = time.perf_counter()
        try:
            response = await asyncio.wait_for(
                self._client.aio.models.generate_content(
                    model=self._settings.gemini_model,
                    contents=parts,
                    config=config,
                ),
                timeout=self._settings.api_timeout + self._timeout_grace_s,
            )
        except genai_errors.APIError as exc:
            raise self._translate_api_error(exc) from exc
        except asyncio.TimeoutError as exc:
            logger.warning("vision_timeout", extra={"error_type": "asyncio.TimeoutError"})
            raise VisionTimeoutError() from exc
        except Exception as exc:  # noqa: BLE001 - last line of defence at provider boundary
            raise self._classify_unexpected(exc) from exc

        duration_ms = round((time.perf_counter() - started) * 1000)

        text = (response.text or "").strip() if response is not None else ""
        if not text:
            logger.warning(
                "vision_empty_response",
                extra={"duration_ms": duration_ms, "image_count": len(images)},
            )
            raise VisionEmptyResponseError()

        candidates = getattr(response, "candidates", None) or []
        finish = getattr(candidates[0], "finish_reason", None) if candidates else None
        if finish is not None and "STOP" not in str(finish).upper():
            # e.g. MAX_TOKENS: the JSON is probably truncated; ListingService will retry/reject.
            logger.warning("vision_incomplete", extra={"finish_reason": str(finish)})

        logger.info(
            "vision_call_ok",
            extra={
                "duration_ms": duration_ms,
                "image_count": len(images),
                "model": self._settings.gemini_model,
            },
        )
        return text

    # ------------------------------------------------------------ text + image generation
    async def _call(self, model: str, contents, config, *, timeout: float):
        """One guarded provider call; every failure becomes a domain exception."""
        try:
            return await asyncio.wait_for(
                self._client.aio.models.generate_content(model=model, contents=contents, config=config),
                timeout=timeout + self._timeout_grace_s,
            )
        except genai_errors.APIError as exc:
            raise self._translate_api_error(exc) from exc
        except asyncio.TimeoutError as exc:
            logger.warning("vision_timeout", extra={"error_type": "asyncio.TimeoutError"})
            raise VisionTimeoutError() from exc
        except Exception as exc:  # noqa: BLE001 - provider boundary
            raise self._classify_unexpected(exc) from exc

    async def complete(self, prompt: str) -> str:
        """Text-only completion that returns raw JSON text."""
        started = time.perf_counter()
        model = self._settings.gemini_text_model or self._settings.gemini_model
        config = types.GenerateContentConfig(
            temperature=0.4,
            max_output_tokens=self._settings.gemini_max_output_tokens,
            response_mime_type="application/json",
        )
        response = await self._call(model, [prompt], config, timeout=self._settings.api_timeout)
        text = (response.text or "").strip() if response is not None else ""
        if not text:
            raise VisionEmptyResponseError()
        logger.info(
            "text_call_ok",
            extra={"duration_ms": round((time.perf_counter() - started) * 1000), "model": model},
        )
        return text

    async def generate_image(self, references: Sequence[ImagePayload], prompt: str) -> bytes:
        """Generate one image conditioned on the reference photos."""
        if not references:
            raise ValueError("At least one reference image is required.")
        started = time.perf_counter()
        contents: list[types.Part | str] = [
            types.Part.from_bytes(data=img.data, mime_type=img.mime_type) for img in references
        ]
        contents.append(prompt)
        config = types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"])
        response = await self._call(
            self._settings.gemini_image_model,
            contents,
            config,
            timeout=self._settings.image_gen_timeout,
        )
        for candidate in getattr(response, "candidates", None) or []:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", None) or []:
                inline = getattr(part, "inline_data", None)
                data = getattr(inline, "data", None)
                if data:
                    logger.info(
                        "image_generated",
                        extra={
                            "duration_ms": round((time.perf_counter() - started) * 1000),
                            "model": self._settings.gemini_image_model,
                        },
                    )
                    return bytes(data)
        logger.warning("image_generation_empty")
        raise VisionEmptyResponseError()

    @staticmethod
    def _translate_api_error(exc: genai_errors.APIError) -> VisionServiceError:
        code = getattr(exc, "code", None)
        # Provider messages describe the failure ("model not found", "API key not
        # valid"); they do not echo credentials. Truncated and flattened for logs.
        message = " ".join(str(getattr(exc, "message", "") or "").split())[:200]
        logger.warning(
            "vision_api_error", extra={"provider_status": code, "provider_message": message}
        )

        if code == 429:
            return VisionRateLimitError()
        if code in (400, 401, 403, 404):  # bad key / forbidden / unknown model / bad request
            return VisionConfigurationError()
        if code in (408, 504):
            return VisionTimeoutError()
        if isinstance(code, int) and code >= 500:
            return VisionUnavailableError()
        return VisionServiceError()

    @staticmethod
    def _classify_unexpected(exc: Exception) -> VisionServiceError:
        names = {cls.__name__ for cls in type(exc).__mro__}
        logger.warning("vision_unexpected_error", extra={"error_type": type(exc).__name__})

        if any("Timeout" in n for n in names):
            return VisionTimeoutError()
        if isinstance(exc, (ConnectionError, OSError)) or any(
            marker in n for n in names for marker in ("Connect", "Network", "Transport")
        ):
            return VisionUnavailableError()
        return VisionServiceError()


class ResilientVision:
    """Wraps any VisionProvider with a concurrency cap and bounded retry.

    Retried (transient, safe): provider 5xx / network failure, empty response.
    NOT retried: rate limit (retrying makes it worse), timeout (would double the
    wait), misconfiguration (will not fix itself).
    """

    _RETRYABLE = (VisionUnavailableError, VisionEmptyResponseError)

    def __init__(
        self,
        inner: VisionProvider,
        settings: Settings,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._inner = inner
        self._retries = settings.vision_max_retries
        self._backoff = settings.vision_retry_backoff_s
        self._sleep = sleep
        self._slots = asyncio.Semaphore(settings.vision_max_concurrency)

    async def analyze(self, images: Sequence[ImagePayload], prompt: str) -> str:
        for attempt in range(self._retries + 1):
            try:
                async with self._slots:  # released while we back off
                    return await self._inner.analyze(images, prompt)
            except self._RETRYABLE as exc:
                if attempt == self._retries:
                    raise
                delay = self._backoff * (2**attempt)
                logger.warning(
                    "vision_retry",
                    extra={"attempt": attempt + 1, "error_code": exc.error_code, "delay_s": delay},
                )
                await self._sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover
