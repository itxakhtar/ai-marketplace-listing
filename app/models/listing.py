"""Validated listing schema. This is the final gate for all LLM output."""

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import Category, Condition

_WS = re.compile(r"\s+")


def _clean(text: str) -> str:
    return _WS.sub(" ", text).strip()


class ListingResponse(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="ignore",  # drop unexpected keys the LLM may add
        json_schema_extra={
            "example": {
                "detected_item": "Samsung Tablet with Stylus",
                "category": "Mobiles and Tablets",
                "condition_estimate": "Unable to Determine",
                "suggested_title": "Samsung Tablet with Stylus - Silver",
                "suggested_description": (
                    "Silver tablet with a visible Samsung logo on the rear panel, "
                    "pictured with a white stylus."
                ),
                "extracted_features": ["Samsung logo on rear panel", "Silver back panel"],
                "tags": ["samsung", "tablet", "stylus", "silver", "touchscreen"],
            }
        },
    )

    detected_item: str = Field(..., min_length=1, max_length=120)
    category: Category
    condition_estimate: Condition
    suggested_title: str = Field(..., min_length=1, max_length=70)
    suggested_description: str = Field(..., min_length=1, max_length=2000)
    extracted_features: list[str] = Field(default_factory=list, max_length=10)
    tags: list[str] = Field(..., min_length=5, max_length=8)

    @field_validator("extracted_features", mode="before")
    @classmethod
    def _normalize_features(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        seen: set[str] = set()
        result: list[str] = []
        for item in value:
            if not isinstance(item, str):
                return value  # let standard validation reject it
            cleaned = _clean(item)
            if cleaned and cleaned.lower() not in seen:
                seen.add(cleaned.lower())
                result.append(cleaned)
        return result

    @field_validator("tags", mode="before")
    @classmethod
    def _normalize_tags(cls, value: object) -> object:
        """Lowercase, strip '#', dedupe, and cap at 8.

        Extra tags are harmless noise, so we truncate. Too few tags is a real
        quality problem, so the min_length=5 constraint still rejects those.
        """
        if not isinstance(value, list):
            return value
        seen: set[str] = set()
        result: list[str] = []
        for item in value:
            if not isinstance(item, str):
                return value
            cleaned = _clean(item).lstrip("#").lower()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                result.append(cleaned)
        return result[:8]
