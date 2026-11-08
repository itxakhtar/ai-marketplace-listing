"""Pure scoring functions for the AI evaluation. No network, no Gemini."""

import re
from dataclasses import dataclass, field

# Brand names the model might hallucinate. Extend freely; matching is whole-word, case-insensitive.
COMMON_BRANDS: frozenset[str] = frozenset(
    """apple iphone ipad macbook samsung galaxy huawei xiaomi oppo vivo realme oneplus google pixel
    sony nikon canon dell hp lenovo asus acer msi microsoft surface logitech jbl bose
    nike adidas puma reebok skechers gucci prada zara levis rolex casio seiko fossil citizen garmin fitbit
    toyota honda suzuki hyundai kia nissan ford bmw mercedes audi volkswagen yamaha kawasaki
    bosch makita dewalt stanley milwaukee ikea""".split()
)

# Specifications that cannot be read off a normal product photo.
SPEC_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(rx, re.IGNORECASE))
    for name, rx in (
        ("storage_or_ram", r"\b\d+\s?(gb|tb|mb)\b"),
        ("battery", r"\b\d{3,5}\s?mah\b"),
        ("megapixels", r"\b\d+(\.\d+)?\s?mp\b"),
        ("refresh_rate", r"\b\d{2,3}\s?hz\b"),
        ("cpu", r"\b(i[3579]-?\d*|ryzen\s?\d|snapdragon|exynos|bionic|m[1-4]\s?(pro|max)?)\b"),
        ("engine", r"\b\d(\.\d)?\s?l\b|\b\d{2,4}\s?(cc|hp|bhp)\b"),
        ("screen_size", r"\b\d{1,2}(\.\d)?\s?(inch|in\b|\")"),
        ("year", r"\b(19|20)\d{2}\s?(model|edition)\b"),
    )
)


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def listing_text(listing: dict) -> str:
    parts = [
        listing.get("detected_item", ""),
        listing.get("suggested_title", ""),
        listing.get("suggested_description", ""),
        *listing.get("extracted_features", []),
        *listing.get("tags", []),
    ]
    return " | ".join(str(p) for p in parts)


def brand_hallucinations(listing: dict, allowed_brands: list[str]) -> list[str]:
    """Common brand names present in the output but not whitelisted for this case."""
    allowed = {b.lower() for b in allowed_brands}
    found = _words(listing_text(listing)) & COMMON_BRANDS
    return sorted(found - allowed)


def spec_hallucinations(listing: dict, allowed_specs: list[str]) -> list[str]:
    text = listing_text(listing)
    allowed = [a.lower() for a in allowed_specs]
    hits = []
    for name, pattern in SPEC_PATTERNS:
        for match in pattern.finditer(text):
            if not any(a in match.group(0).lower() for a in allowed):
                hits.append(f"{name}: {match.group(0).strip()}")
    return hits


def tag_relevance(listing: dict, required_any: list[str]) -> bool | None:
    """True if at least one expected keyword appears in tags/title/item. None = not scored."""
    if not required_any:
        return None
    haystack = " ".join([listing.get("detected_item", ""), listing.get("suggested_title", ""), *listing.get("tags", [])]).lower()
    return any(term.lower() in haystack for term in required_any)


@dataclass
class CaseResult:
    case_id: str
    kind: str  # "product" | "difficult"
    status: str  # "ok" | "error" | "skipped"
    error_code: str | None = None
    listing: dict | None = None
    checks: dict[str, bool | None] = field(default_factory=dict)
    details: dict[str, object] = field(default_factory=dict)

    @property
    def failed_checks(self) -> list[str]:
        return [name for name, ok in self.checks.items() if ok is False]


def score_case(case: dict, outcome: dict) -> CaseResult:
    """Score one case. `outcome` is {"listing": dict} or {"error_code": str} or {"skipped": str}."""
    result = CaseResult(case["id"], case.get("kind", "product"), "ok")
    if "skipped" in outcome:
        result.status = "skipped"
        result.details["reason"] = outcome["skipped"]
        return result

    expected_error = case.get("expect_error")
    if "error_code" in outcome:
        result.status = "error"
        result.error_code = outcome["error_code"]
        # An expected, clean refusal is a pass. ai_output_invalid is always a failure.
        result.checks["json_valid"] = outcome["error_code"] != "ai_output_invalid"
        result.checks["expected_outcome"] = expected_error == outcome["error_code"]
        return result

    listing = outcome["listing"]
    result.listing = listing
    result.checks["json_valid"] = True
    result.checks["expected_outcome"] = expected_error is None  # produced a listing but an error was expected
    if expected_error is not None:
        return result

    cats = case.get("expected_categories")
    result.checks["category_correct"] = (listing["category"] in cats) if cats else None
    result.checks["title_max_70"] = len(listing["suggested_title"]) <= 70
    result.checks["tag_count_5_to_8"] = 5 <= len(listing["tags"]) <= 8
    result.checks["tag_relevant"] = tag_relevance(listing, case.get("required_any", []))
    conditions = case.get("acceptable_conditions")
    result.checks["condition_reasonable"] = (listing["condition_estimate"] in conditions) if conditions else None

    brands = brand_hallucinations(listing, case.get("allowed_brands", []))
    specs = spec_hallucinations(listing, case.get("allowed_specs", []))
    result.checks["no_brand_hallucination"] = not brands
    result.checks["no_spec_hallucination"] = not specs
    result.details.update(brand_mentions=brands, spec_mentions=specs)
    return result


def summarize(results: list[CaseResult]) -> dict[str, dict[str, float | int]]:
    """Per-check pass counts over scored (non-skipped) cases; None-valued checks are excluded."""
    names: list[str] = []
    for r in results:
        for n in r.checks:
            if n not in names:
                names.append(n)
    summary = {}
    for name in names:
        scored = [r.checks[name] for r in results if r.status != "skipped" and r.checks.get(name) is not None]
        passed = sum(1 for v in scored if v)
        summary[name] = {
            "passed": passed,
            "total": len(scored),
            "rate": round(passed / len(scored), 3) if scored else 0.0,
        }
    return summary
