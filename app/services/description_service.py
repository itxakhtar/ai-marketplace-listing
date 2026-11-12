"""Expands a short, verified listing into a longer marketplace description.

Text-only: it can restate the supplied facts but never invent new ones. The output is
checked by the same deterministic guardrails as listings, plus a "no new numbers" rule.
"""

import logging

from app.core.config import Settings
from app.core.exceptions import ListingValidationError
from app.models.extras import ExpandDescriptionRequest, ExpandedDescription
from app.prompts.description_prompt import build_expand_prompt, facts_text
from app.services.guardrails import GuardrailViolation, check_text, retry_feedback
from app.services.listing_service import parse_llm_json
from app.services.vision_service import TextProvider

logger = logging.getLogger(__name__)

MIN_LENGTH = 200


class DescriptionService:
    def __init__(self, text: TextProvider, settings: Settings) -> None:
        self._text = text
        self._settings = settings

    async def expand(self, request: ExpandDescriptionRequest) -> ExpandedDescription:
        source = facts_text(request)
        feedback: str | None = None
        attempts = self._settings.max_ai_attempts

        for attempt in range(1, attempts + 1):
            raw = await self._text.complete(build_expand_prompt(request, feedback))
            try:
                text = self._validate(raw, source)
                logger.info("description_expanded", extra={"attempt": attempt, "chars": len(text)})
                return ExpandedDescription(long_description=text)
            except (ListingValidationError, GuardrailViolation) as exc:
                reason = f"guardrail:{exc}" if isinstance(exc, GuardrailViolation) else str(exc)
                logger.warning(
                    "description_rejected", extra={"attempt": attempt, "attempts": attempts, "reason": reason}
                )
                if attempt == attempts:
                    raise ListingValidationError() from exc
                feedback = retry_feedback(exc.rules) if isinstance(exc, GuardrailViolation) else None
        raise AssertionError("unreachable")  # pragma: no cover

    def _validate(self, raw: str, source: str) -> str:
        data = parse_llm_json(raw)
        value = data.get("long_description")
        if not isinstance(value, str):
            raise ListingValidationError("Missing long_description")
        text = "\n".join(line.rstrip() for line in value.strip().splitlines()).strip()
        if len(text) < MIN_LENGTH or len(text) > 4000:
            raise ListingValidationError("long_description has an unusable length")
        check_text(text, source=source, block_unverified_specs=self._settings.block_unverified_specs)
        return text
