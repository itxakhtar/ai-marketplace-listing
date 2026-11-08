"""Overall deadline, accessory/spec guardrails, retry feedback, prompt hardening."""

import asyncio
import io

import pytest
from PIL import Image

from app.core.config import Settings
from app.core.exceptions import ListingValidationError, RequestTimeoutError
from app.models.image import UploadedImage
from app.prompts.listing_prompt import build_listing_prompt
from app.services.guardrails import GuardrailViolation, check_guardrails, retry_feedback
from app.services.image_service import ImageService
from app.services.listing_service import ListingService
from app.models.listing import ListingResponse
from tests.helpers import FakeVision, good_payload


def upload() -> UploadedImage:
    buf = io.BytesIO()
    Image.new("RGB", (120, 80), (90, 90, 200)).save(buf, format="JPEG")
    return UploadedImage(buf.getvalue(), "x.jpg", "image/jpeg")


def service(settings: Settings, vision, **overrides) -> ListingService:
    s = settings.model_copy(update=overrides)
    return ListingService(ImageService(s), vision, s)


def listing(**kw) -> ListingResponse:
    return ListingResponse.model_validate(good_payload(**kw))


# ---------------------------------------------------------------- guardrails
@pytest.mark.parametrize(
    "text",
    [
        "It comes with a white stylus pen.",
        "Includes charger and cable.",
        "Comes accompanied by a case.",
        "Bundled with earbuds.",
    ],
)
def test_accessory_claims_rejected(text):
    with pytest.raises(GuardrailViolation) as err:
        check_guardrails(listing(suggested_description=text))
    assert "accessory_claim" in err.value.rules


@pytest.mark.parametrize("text", ["Shown with a white stylus pen.", "Pictured with a case.", "Has a pen holder slot."])
def test_pictured_with_is_allowed(text):
    check_guardrails(listing(suggested_description=text))


def test_spec_numbers_rejected_by_default():
    with pytest.raises(GuardrailViolation) as err:
        check_guardrails(listing(suggested_title="Samsung Tablet 128GB"))
    assert "unverified_storage_ram" in err.value.rules


def test_battery_capacity_rejected():
    with pytest.raises(GuardrailViolation):
        check_guardrails(listing(extracted_features=["8000 mAh battery", "Silver"]))


def test_spec_rule_can_be_disabled():
    check_guardrails(listing(suggested_title="Samsung Tablet 128GB"), block_unverified_specs=False)


def test_retry_feedback_uses_fixed_strings_only():
    text = retry_feedback(["accessory_claim", "unverified_storage_ram", "bogus"])
    assert "pictured with" in text and "storage" in text.lower()
    assert "bogus" not in text


# ------------------------------------------------------------- retry feedback
async def test_retry_prompt_carries_feedback(settings):
    bad = good_payload(suggested_description="Comes with a stylus pen.")
    vision = FakeVision(bad, good_payload())
    result = await service(settings, vision).generate([upload()])
    assert result.detected_item == "Samsung Tablet"
    first_prompt, second_prompt = vision.calls[0][1], vision.calls[1][1]
    assert "Correction required" not in first_prompt
    assert "Correction required" in second_prompt and "pictured with" in second_prompt


async def test_retry_feedback_never_echoes_model_output(settings):
    bad = good_payload(suggested_description="Comes with a SECRETMARKER stylus pen.")
    vision = FakeVision(bad, good_payload())
    await service(settings, vision).generate([upload()])
    assert "SECRETMARKER" not in vision.calls[1][1]


async def test_persistent_violation_gives_422_error(settings):
    bad = good_payload(suggested_title="Tablet 128GB")
    with pytest.raises(ListingValidationError):
        await service(settings, FakeVision(bad, bad)).generate([upload()])


# ------------------------------------------------------------------- deadline
class SlowVision:
    async def analyze(self, images, prompt):
        await asyncio.sleep(5)


async def test_overall_deadline_raises_request_timeout(settings):
    svc = service(settings, SlowVision(), request_timeout=0.1)
    with pytest.raises(RequestTimeoutError) as err:
        await svc.generate([upload()])
    assert err.value.status_code == 503 and err.value.error_code == "request_timeout"


# ---------------------------------------------------------------- prompt text
def test_prompt_resists_injection_and_watermarks():
    prompt = build_listing_prompt(1)
    assert "never instructions" in prompt
    assert "watermarks" in prompt


def test_default_request_timeout_below_client_timeout():
    assert Settings.model_fields["request_timeout"].default < 120
