"""Deterministic content rules applied to every model answer.

The prompt asks the model to behave; these rules *verify* it. Each rule has a fixed
name and a fixed, model-safe instruction, so a rejection can be fed back to the model
on retry without ever echoing model output or provider text.
"""

import re
from dataclasses import dataclass

from app.models.listing import ListingResponse


class GuardrailViolation(Exception):
    """Model output parsed fine but broke a content rule (message = comma-joined rule names)."""

    def __init__(self, rules: list[str]) -> None:
        self.rules = rules
        super().__init__(",".join(rules))


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]
    instruction: str


def _rule(name: str, pattern: str, instruction: str) -> Rule:
    return Rule(name, re.compile(pattern, re.IGNORECASE), instruction)


_CLAIM = "Do not claim this; it cannot be verified from a photo."
CONTENT_RULES: tuple[Rule, ...] = (
    _rule("fully_functional", r"\bfully (functional|working|operational)\b", _CLAIM),
    _rule("works_perfectly", r"\bworks? (perfectly|great|flawlessly)\b", _CLAIM),
    _rule("unlocked", r"\bunlocked\b", _CLAIM),
    _rule("warranty", r"\bwarranty\b", _CLAIM),
    _rule("battery_health", r"\bbattery (health|life)\b", _CLAIM),
    _rule("genuine", r"\bgenuine\b", _CLAIM),
    _rule("like_brand_new", r"\blike brand new\b", _CLAIM),
    _rule("hype", r"\b(amazing|best deal|mint condition)\b|100%", "Remove hype wording."),
    _rule("exclamation", r"!", "Do not use exclamation marks."),
    _rule(
        "accessory_claim",
        r"\b(includes?|including|comes? (with|accompanied)|bundled with)\b[^.|]{0,40}"
        r"\b(charger|cable|case|cover|box|stylus|pen|earbuds?|headphones?|adapter|remote|manual|strap)\b",
        'A photo does not prove an accessory is part of the sale. Use "pictured with" or '
        '"shown with" instead of "includes" / "comes with".',
    ),
)

# Numbers that are almost never legible in a normal product photo.
SPEC_RULES: tuple[Rule, ...] = (
    _rule(
        "unverified_storage_ram",
        r"\b\d{1,4}\s?(gb|tb)\b",
        "Do not state storage or RAM sizes unless printed text in the photo shows them.",
    ),
    _rule(
        "unverified_battery_capacity",
        r"\b\d{3,5}\s?mah\b",
        "Do not state battery capacity.",
    ),
)

_INSTRUCTIONS = {r.name: r.instruction for r in (*CONTENT_RULES, *SPEC_RULES)}


def check_guardrails(listing: ListingResponse, *, block_unverified_specs: bool = True) -> None:
    text = " | ".join(
        [listing.suggested_title, listing.suggested_description, *listing.extracted_features]
    )
    rules = CONTENT_RULES + (SPEC_RULES if block_unverified_specs else ())
    violated = [r.name for r in rules if r.pattern.search(text)]
    if violated:
        raise GuardrailViolation(violated)


def retry_feedback(rules: list[str]) -> str:
    """Fixed-string guidance for the retry prompt; never contains model output."""
    lines = [f"- {_INSTRUCTIONS[name]}" for name in rules if name in _INSTRUCTIONS]
    return "\n".join(dict.fromkeys(lines))
