"""Schemas for the description-expansion and related-images features."""

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import Category, Condition


class ExpandDescriptionRequest(BaseModel):
    """The (possibly user-edited) listing facts. Treated as untrusted data."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    detected_item: str = Field(..., min_length=1, max_length=120)
    category: Category
    condition_estimate: Condition
    suggested_title: str = Field(..., min_length=1, max_length=70)
    suggested_description: str = Field(..., min_length=1, max_length=2000)
    extracted_features: list[str] = Field(default_factory=list, max_length=10)
    tags: list[str] = Field(default_factory=list, max_length=8)


class ExpandedDescription(BaseModel):
    long_description: str = Field(..., min_length=1, max_length=4000)


class RelatedImage(BaseModel):
    label: str = Field(..., examples=["Studio view"])
    mime_type: str = "image/jpeg"
    data_base64: str
    ai_generated: bool = True


AI_IMAGE_NOTICE = (
    "AI-generated illustrations based on your photo. They are not photos of the actual item: "
    "do not present them to buyers as real photos."
)


class RelatedImagesResponse(BaseModel):
    images: list[RelatedImage]
    notice: str = AI_IMAGE_NOTICE
