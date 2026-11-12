"""Description expansion + related images: services and HTTP endpoints (providers mocked)."""

import base64
import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.core.config import Settings
from app.core.exceptions import (
    ListingValidationError,
    VisionConfigurationError,
    VisionEmptyResponseError,
)
from app.main import create_app
from app.models.extras import ExpandDescriptionRequest
from app.services.description_service import DescriptionService
from app.services.guardrails import GuardrailViolation, check_text
from app.services.image_service import ImageService
from app.services.related_images_service import RelatedImagesService
from tests.helpers import jpeg_bytes

REQ = {
    "detected_item": "Samsung Tablet",
    "category": "Mobiles and Tablets",
    "condition_estimate": "Unable to Determine",
    "suggested_title": "Samsung Tablet - Silver",
    "suggested_description": "Silver tablet with a visible Samsung logo on the rear panel.",
    "extracted_features": ["Samsung logo on rear panel", "Silver back panel", "Single rear camera"],
    "tags": ["samsung", "tablet", "silver", "touchscreen", "android"],
}

LONG = (
    "This silver Samsung tablet shows a clear Samsung logo on its rear panel. It suits anyone "
    "looking for a simple touchscreen device for browsing, reading and video.\n\n"
    "Highlights\n• Samsung logo on rear panel\n• Silver back panel\n• Single rear camera\n\n"
    "Condition\nThe condition is not stated. Please look closely at the photos before buying.\n\n"
    "Feel free to ask any questions about this tablet."
)


class FakeText:
    def __init__(self, *responses):
        self.responses, self.prompts = list(responses), []

    async def complete(self, prompt):
        self.prompts.append(prompt)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, str) else json.dumps(item)


class FakeImageGen:
    def __init__(self, *items):
        self.items, self.calls = list(items), []

    async def generate_image(self, references, prompt):
        self.calls.append((list(references), prompt))
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def png(size=(300, 200), color=(200, 40, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def desc_service(settings, *responses):
    text = FakeText(*responses)
    return DescriptionService(text, settings), text


# ------------------------------------------------------------------ guardrails
def test_check_text_blocks_numbers_not_in_source():
    with pytest.raises(GuardrailViolation) as err:
        check_text("A tablet with 12 hours of use.", source="A tablet")
    assert "invented_number" in err.value.rules


def test_check_text_allows_numbers_present_in_source():
    check_text("Galaxy Tab S10 with a 10 inch screen", source="Galaxy Tab S10 | 10 inch screen")


def test_check_text_spec_allowed_only_if_in_source():
    check_text("Shows 128GB on the box", source="box text: 128 GB", block_unverified_specs=True)
    with pytest.raises(GuardrailViolation):
        check_text("A 128GB tablet", source="tablet")


# ---------------------------------------------------------------- DescriptionService
async def test_expand_returns_long_description(settings):
    svc, text = desc_service(settings, {"long_description": LONG})
    result = await svc.expand(ExpandDescriptionRequest(**REQ))
    assert result.long_description.startswith("This silver Samsung tablet")
    assert "Samsung Tablet" in text.prompts[0] and "data only" in text.prompts[0]


async def test_expand_rejects_invented_number_then_retries_with_feedback(settings):
    bad = {"long_description": LONG + " Battery lasts 12 hours."}
    svc, text = desc_service(settings, bad, {"long_description": LONG})
    result = await svc.expand(ExpandDescriptionRequest(**REQ))
    assert result.long_description == LONG
    assert "Correction required" in text.prompts[1] and "numbers" in text.prompts[1]
    assert "12 hours" not in text.prompts[1]  # never echoes model output


async def test_expand_rejects_accessory_claim_and_too_short(settings):
    bad = {"long_description": LONG.replace("Feel free", "It comes with a stylus pen. Feel free")}
    svc, _ = desc_service(settings, bad, {"long_description": "too short"})
    with pytest.raises(ListingValidationError):
        await svc.expand(ExpandDescriptionRequest(**REQ))


async def test_expand_rejects_non_json_and_missing_key(settings):
    svc, _ = desc_service(settings, "not json", {"other": "x"})
    with pytest.raises(ListingValidationError):
        await svc.expand(ExpandDescriptionRequest(**REQ))


async def test_expand_propagates_provider_errors(settings):
    svc, _ = desc_service(settings, VisionConfigurationError())
    with pytest.raises(VisionConfigurationError):
        await svc.expand(ExpandDescriptionRequest(**REQ))


def test_expand_request_validates_taxonomy():
    with pytest.raises(Exception):
        ExpandDescriptionRequest(**{**REQ, "category": "Spaceships"})


# ------------------------------------------------------------- RelatedImagesService
def images_service(settings, *items, **overrides):
    s = settings.model_copy(update=overrides)
    gen = FakeImageGen(*items)
    return RelatedImagesService(ImageService(s), gen, s), gen


async def test_generates_three_labelled_jpegs():
    from app.models.image import UploadedImage

    s = Settings(gemini_api_key="k", _env_file=None)
    svc, gen = images_service(s, png(), png((640, 480)), png((90, 90)))
    out = await svc.generate([UploadedImage(jpeg_bytes(), "a.jpg", "image/jpeg")], "Samsung Tablet")
    assert [i.label for i in out.images] == ["Studio view", "Alternate angle", "In context"]
    assert all(i.ai_generated for i in out.images) and "not photos" in out.notice
    decoded = Image.open(io.BytesIO(base64.b64decode(out.images[0].data_base64)))
    assert decoded.format == "JPEG"
    assert "Samsung Tablet" in gen.calls[0][1] and "SAME product" in gen.calls[0][1]


async def test_watermark_changes_pixels_and_can_be_disabled():
    from app.models.image import UploadedImage

    up = [UploadedImage(jpeg_bytes(), "a.jpg", "image/jpeg")]
    marked, _ = images_service(Settings(gemini_api_key="k", _env_file=None), png(), png(), png())
    plain, _ = images_service(
        Settings(gemini_api_key="k", _env_file=None), png(), png(), png(), watermark_generated_images=False
    )
    a = Image.open(io.BytesIO(base64.b64decode((await marked.generate(up)).images[0].data_base64)))
    b = Image.open(io.BytesIO(base64.b64decode((await plain.generate(up)).images[0].data_base64)))
    corner = (6, a.height - 30, 80, a.height - 4)
    assert a.crop(corner).tobytes() != b.crop(corner).tobytes()


async def test_partial_failure_returns_remaining_images():
    from app.models.image import UploadedImage

    s = Settings(gemini_api_key="k", _env_file=None)
    svc, _ = images_service(s, png(), VisionEmptyResponseError(), b"not an image")
    out = await svc.generate([UploadedImage(jpeg_bytes(), "a.jpg", "image/jpeg")])
    assert [i.label for i in out.images] == ["Studio view"]


async def test_all_failures_raise_domain_error():
    from app.models.image import UploadedImage

    s = Settings(gemini_api_key="k", _env_file=None)
    err = VisionConfigurationError()
    svc, _ = images_service(s, err, err, err)
    with pytest.raises(VisionConfigurationError):
        await svc.generate([UploadedImage(jpeg_bytes(), "a.jpg", "image/jpeg")])


async def test_count_setting_limits_requests():
    from app.models.image import UploadedImage

    s = Settings(gemini_api_key="k", _env_file=None)
    svc, gen = images_service(s, png(), related_image_count=1)
    out = await svc.generate([UploadedImage(jpeg_bytes(), "a.jpg", "image/jpeg")])
    assert len(out.images) == 1 and len(gen.calls) == 1


# ----------------------------------------------------------------------- HTTP
def build_client(text=None, gen=None, **kw) -> TestClient:
    settings = Settings(gemini_api_key="k", _env_file=None, **kw)
    images = ImageService(settings)
    app = create_app(
        settings=settings,
        listing_service=object(),
        description_service=DescriptionService(text or FakeText(), settings),
        related_images_service=RelatedImagesService(images, gen or FakeImageGen(), settings),
    )
    return TestClient(app)


def test_expand_endpoint_ok():
    r = build_client(text=FakeText({"long_description": LONG})).post("/api/v1/expand-description/", json=REQ)
    assert r.status_code == 200 and "Highlights" in r.json()["long_description"]


def test_expand_endpoint_validation_and_disabled():
    c = build_client()
    bad = c.post("/api/v1/expand-description/", json={"detected_item": "x"})
    assert bad.status_code == 400 and "JSON" in bad.json()["error"]["message"]
    r = build_client(enable_description_expansion=False).post("/api/v1/expand-description/", json=REQ)
    assert r.status_code == 404 and r.json()["error"]["code"] == "feature_disabled"


def test_expand_endpoint_ai_failure_is_422_without_leak():
    c = build_client(text=FakeText("junk", "junk"))
    r = c.post("/api/v1/expand-description/", json=REQ)
    assert r.status_code == 422 and r.json()["error"]["code"] == "ai_output_invalid"


def test_generate_images_endpoint_ok_and_item_name_forwarded():
    gen = FakeImageGen(png(), png(), png())
    r = build_client(gen=gen).post(
        "/api/v1/generate-images/",
        files=[("images", ("a.jpg", jpeg_bytes(), "image/jpeg"))],
        data={"detected_item": "Samsung Tablet"},
    )
    assert r.status_code == 200 and len(r.json()["images"]) == 3
    assert "Samsung Tablet" in gen.calls[0][1]


def test_generate_images_errors():
    one = [("images", ("a.jpg", jpeg_bytes(), "image/jpeg"))]
    r = build_client(enable_image_generation=False).post("/api/v1/generate-images/", files=one)
    assert r.status_code == 404
    err = VisionConfigurationError()
    r = build_client(gen=FakeImageGen(err, err, err)).post("/api/v1/generate-images/", files=one)
    assert r.status_code == 503 and r.json()["error"]["code"] == "vision_misconfigured"
    r = build_client().post("/api/v1/generate-images/", files=[("images", ("a.txt", b"hello", "image/jpeg"))])
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_image"


def test_docs_can_be_hidden():
    assert build_client().get("/docs").status_code == 200
    hidden = build_client(enable_docs=False)
    assert hidden.get("/docs").status_code == 404
    assert hidden.get("/openapi.json").status_code == 404
    assert hidden.get("/health").status_code == 200
