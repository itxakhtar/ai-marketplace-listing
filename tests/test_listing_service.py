import io
import json

import pytest
from PIL import Image

from app.core.config import Settings
from app.core.exceptions import (
    ImageTooLargeError,
    InvalidImageError,
    ListingValidationError,
    NoProductDetectedError,
    VisionRateLimitError,
)
from app.models.enums import Category, Condition
from app.models.image import UploadedImage
from app.services.image_service import ImageService
from app.services.listing_service import ListingService, parse_llm_json


def good(**overrides) -> dict:
    data = {
        "detected_item": "Samsung Tablet",
        "category": "Mobiles and Tablets",
        "condition_estimate": "Unable to Determine",
        "suggested_title": "Samsung Tablet - Silver",
        "suggested_description": "Silver tablet with a visible Samsung logo on the rear panel.",
        "extracted_features": ["Samsung logo on rear", "Silver back panel"],
        "tags": ["samsung", "tablet", "silver", "touchscreen", "android"],
    }
    data.update(overrides)
    return data


class FakeVision:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def analyze(self, images, prompt):
        self.calls.append((list(images), prompt))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, str) else json.dumps(item)


def jpeg() -> UploadedImage:
    buf = io.BytesIO()
    Image.new("RGB", (120, 80), (90, 90, 200)).save(buf, format="JPEG")
    return UploadedImage(buf.getvalue(), "x.jpg", "image/jpeg")


def make(settings: Settings, vision: FakeVision) -> ListingService:
    return ListingService(ImageService(settings), vision, settings)


# ------------------------------------------------------------------ parsing
def test_parse_plain_json():
    assert parse_llm_json('{"a": 1}') == {"a": 1}


def test_parse_strips_code_fences():
    assert parse_llm_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_extracts_object_from_chatter():
    assert parse_llm_json('Sure! Here it is: {"a": 1} Hope that helps.') == {"a": 1}


@pytest.mark.parametrize("raw", ["", "not json", "[1, 2]", '{"a": '])
def test_parse_rejects_garbage(raw):
    with pytest.raises(ListingValidationError):
        parse_llm_json(raw)


# ----------------------------------------------------------------- happy path
async def test_valid_response_returns_listing(settings):
    vision = FakeVision(good())
    listing = await make(settings, vision).generate([jpeg()])
    assert listing.category is Category.MOBILES_AND_TABLETS
    assert listing.condition_estimate is Condition.UNABLE_TO_DETERMINE
    assert len(vision.calls) == 1


async def test_multiple_images_sent_together_with_multi_image_prompt(settings):
    vision = FakeVision(good())
    await make(settings, vision).generate([jpeg(), jpeg(), jpeg()])
    images, prompt = vision.calls[0]
    assert len(images) == 3 and "3 images" in prompt and "SAME" in prompt


async def test_fenced_json_from_model_is_accepted(settings):
    vision = FakeVision("```json\n" + json.dumps(good()) + "\n```")
    assert await make(settings, vision).generate([jpeg()])


async def test_category_and_condition_labels_are_dropped_from_tags(settings):
    tags = ["samsung", "tablet", "mobiles and tablets", "silver", "stylus", "touchscreen"]
    listing = await make(settings, FakeVision(good(tags=tags))).generate([jpeg()])
    assert "mobiles and tablets" not in listing.tags and len(listing.tags) == 5


# --------------------------------------------------------- retries / failures
async def test_malformed_then_valid_succeeds_on_retry(settings):
    vision = FakeVision("garbage", good())
    assert await make(settings, vision).generate([jpeg()])
    assert len(vision.calls) == 2


async def test_persistently_malformed_output_raises_422_error(settings):
    vision = FakeVision("garbage", "still garbage")
    with pytest.raises(ListingValidationError):
        await make(settings, vision).generate([jpeg()])
    assert len(vision.calls) == 2


async def test_schema_violation_title_too_long_is_retried_then_fails(settings):
    bad = good(suggested_title="x" * 80)
    with pytest.raises(ListingValidationError):
        await make(settings, FakeVision(bad, bad)).generate([jpeg()])


async def test_invalid_category_from_model_is_rejected(settings):
    bad = good(category="Pets")
    with pytest.raises(ListingValidationError):
        await make(settings, FakeVision(bad, bad)).generate([jpeg()])


@pytest.mark.parametrize(
    "field,value",
    [
        ("suggested_description", "Fully functional tablet in great shape."),
        ("suggested_description", "Comes with a one year warranty."),
        ("suggested_title", "Samsung Tablet - Best Deal!"),
        ("extracted_features", ["Battery health 95%"]),
        ("suggested_description", "Unlocked and ready to use."),
    ],
)
async def test_unsupported_claims_are_blocked_by_guardrails(settings, field, value):
    bad = good(**{field: value})
    with pytest.raises(ListingValidationError):
        await make(settings, FakeVision(bad, bad)).generate([jpeg()])


async def test_guardrail_violation_then_clean_retry_succeeds(settings):
    vision = FakeVision(good(suggested_description="Fully functional."), good())
    assert await make(settings, vision).generate([jpeg()])


async def test_single_attempt_setting_disables_retry():
    s = Settings(gemini_api_key="k", max_ai_attempts=1, _env_file=None)
    vision = FakeVision("garbage", good())
    with pytest.raises(ListingValidationError):
        await make(s, vision).generate([jpeg()])
    assert len(vision.calls) == 1


async def test_no_product_signal_raises_without_retry(settings):
    vision = FakeVision({"error": "no_identifiable_product"}, good())
    with pytest.raises(NoProductDetectedError):
        await make(settings, vision).generate([jpeg()])
    assert len(vision.calls) == 1


async def test_vision_errors_propagate_unchanged(settings):
    with pytest.raises(VisionRateLimitError):
        await make(settings, FakeVision(VisionRateLimitError())).generate([jpeg()])


# ---------------------------------------------------------------- image input
async def test_zero_images_rejected(settings):
    with pytest.raises(InvalidImageError):
        await make(settings, FakeVision()).generate([])


async def test_too_many_images_rejected(settings):
    uploads = [jpeg()] * (settings.max_images_per_request + 1)
    with pytest.raises(InvalidImageError, match="Too many"):
        await make(settings, FakeVision()).generate(uploads)


async def test_invalid_image_never_reaches_vision_and_names_the_image(settings):
    vision = FakeVision(good())
    with pytest.raises(InvalidImageError, match="Image 2"):
        await make(settings, vision).generate([jpeg(), UploadedImage(b"nope", "bad.jpg")])
    assert vision.calls == []


async def test_oversized_image_keeps_413_error_type(settings):
    s = Settings(gemini_api_key="k", max_image_size_mb=1, _env_file=None)
    big = UploadedImage(b"x" * (1024 * 1024 + 1))
    with pytest.raises(ImageTooLargeError):
        await make(s, FakeVision(good())).generate([big])
