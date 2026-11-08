"""Day 5: quality heuristics, condition downgrade, retries, concurrency, request-size guard."""

import asyncio
import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFilter

from app.core.config import Settings
from app.core.exceptions import (
    VisionConfigurationError,
    VisionEmptyResponseError,
    VisionRateLimitError,
    VisionTimeoutError,
    VisionUnavailableError,
)
from app.main import create_app
from app.models.image import ImagePayload, ImageQuality, UploadedImage
from app.prompts.listing_prompt import build_listing_prompt
from app.services.image_quality import assess_quality
from app.services.image_service import ImageService
from app.services.listing_service import ListingService
from app.services.vision_service import ResilientVision
from tests.helpers import FakeVision, good_payload


def scene(w=800, h=600) -> Image.Image:
    import random

    rnd = random.Random(3)
    img = Image.new("RGB", (w, h), (235, 235, 235))
    d = ImageDraw.Draw(img)
    for _ in range(40):
        x, y = rnd.randint(0, max(1, w - 80)), rnd.randint(0, max(1, h - 80))
        d.rectangle([x, y, x + 120, y + 80], fill=tuple(rnd.randint(20, 200) for _ in range(3)), outline=(0, 0, 0), width=2)
    return img


def as_jpeg(img: Image.Image) -> UploadedImage:
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return UploadedImage(buf.getvalue(), "x.jpg", "image/jpeg")


# ------------------------------------------------------------ quality heuristics
def test_sharp_normal_image_is_not_flagged(settings):
    q = assess_quality(scene(), settings)
    assert not q.is_poor and q.problems == []


def test_dark_image_flagged(settings):
    assert assess_quality(scene().point(lambda v: int(v * 0.1)), settings).is_dark


def test_blurry_image_flagged_and_mild_blur_is_not(settings):
    assert assess_quality(scene().filter(ImageFilter.GaussianBlur(10)), settings).is_blurry
    assert not assess_quality(scene().filter(ImageFilter.GaussianBlur(1)), settings).is_blurry


def test_low_resolution_flagged(settings):
    assert assess_quality(scene(200, 150), settings).is_low_res


def test_sharpness_is_not_inflated_by_unfiltered_border(settings):
    # A flat grey image has zero detail; a bright border artefact would make it look sharp.
    assert assess_quality(Image.new("RGB", (600, 600), (250, 250, 250)), settings).sharpness < 1


def test_thresholds_are_configurable():
    strict = Settings(gemini_api_key="k", low_res_edge=2000, _env_file=None)
    assert assess_quality(scene(), strict).is_low_res


def test_image_service_attaches_quality(settings):
    payload = ImageService(settings).process(as_jpeg(scene().point(lambda v: int(v * 0.1))))
    assert payload.quality is not None and payload.quality.is_dark


# -------------------------------------------------- prompt notes + condition downgrade
def test_prompt_has_quality_section_only_when_notes_given():
    assert "Image quality notes" not in build_listing_prompt(1)
    prompt = build_listing_prompt(2, ["Image 2 appears very dark."])
    assert "Image quality notes" in prompt and "Image 2 appears very dark." in prompt


def make_service(settings, vision) -> ListingService:
    return ListingService(ImageService(settings), vision, settings)


async def test_poor_image_adds_note_to_prompt(settings):
    vision = FakeVision(good_payload())
    await make_service(settings, vision).generate([as_jpeg(scene().point(lambda v: int(v * 0.1)))])
    assert "very dark" in vision.calls[0][1]


async def test_all_poor_images_force_condition_to_unable_to_determine(settings):
    vision = FakeVision(good_payload(condition_estimate="Like New"))
    dark = as_jpeg(scene().point(lambda v: int(v * 0.1)))
    listing = await make_service(settings, vision).generate([dark, dark])
    assert listing.condition_estimate.value == "Unable to Determine"


async def test_one_good_image_keeps_models_condition(settings):
    vision = FakeVision(good_payload(condition_estimate="Used - Good Condition"))
    dark = as_jpeg(scene().point(lambda v: int(v * 0.1)))
    listing = await make_service(settings, vision).generate([dark, as_jpeg(scene())])
    assert listing.condition_estimate.value == "Used - Good Condition"


async def test_good_images_are_untouched(settings):
    vision = FakeVision(good_payload(condition_estimate="Like New"))
    listing = await make_service(settings, vision).generate([as_jpeg(scene())])
    assert listing.condition_estimate.value == "Like New"
    assert "Image quality notes" not in vision.calls[0][1]


# --------------------------------------------------------------- ResilientVision
IMG = ImagePayload(b"x", "image/jpeg")


class Scripted:
    def __init__(self, *results):
        self.results, self.calls = list(results), 0

    async def analyze(self, images, prompt):
        self.calls += 1
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def resilient(inner, retries=1, backoff=0.5, concurrency=4):
    s = Settings(
        gemini_api_key="k",
        vision_max_retries=retries,
        vision_retry_backoff_s=backoff,
        vision_max_concurrency=concurrency,
        _env_file=None,
    )
    sleeps: list[float] = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    return ResilientVision(inner, s, sleep=fake_sleep), sleeps


async def test_transient_error_is_retried_once_then_succeeds():
    inner = Scripted(VisionUnavailableError(), "ok")
    vision, sleeps = resilient(inner)
    assert await vision.analyze([IMG], "p") == "ok"
    assert inner.calls == 2 and sleeps == [0.5]


async def test_empty_response_is_retried():
    inner = Scripted(VisionEmptyResponseError(), "ok")
    assert await resilient(inner)[0].analyze([IMG], "p") == "ok"


async def test_retries_are_bounded_and_last_error_is_raised():
    inner = Scripted(VisionUnavailableError(), VisionUnavailableError(), "never")
    vision, sleeps = resilient(inner, retries=1)
    with pytest.raises(VisionUnavailableError):
        await vision.analyze([IMG], "p")
    assert inner.calls == 2


async def test_backoff_doubles():
    inner = Scripted(*[VisionUnavailableError()] * 3, "ok")
    vision, sleeps = resilient(inner, retries=3, backoff=1.0)
    await vision.analyze([IMG], "p")
    assert sleeps == [1.0, 2.0, 4.0]


@pytest.mark.parametrize("error", [VisionRateLimitError(), VisionTimeoutError(), VisionConfigurationError()])
async def test_non_transient_errors_are_not_retried(error):
    inner = Scripted(error, "never")
    vision, sleeps = resilient(inner)
    with pytest.raises(type(error)):
        await vision.analyze([IMG], "p")
    assert inner.calls == 1 and sleeps == []


async def test_zero_retries_means_single_attempt():
    inner = Scripted(VisionUnavailableError(), "ok")
    with pytest.raises(VisionUnavailableError):
        await resilient(inner, retries=0)[0].analyze([IMG], "p")
    assert inner.calls == 1


async def test_concurrency_is_capped():
    state = {"now": 0, "peak": 0}

    class Slow:
        async def analyze(self, images, prompt):
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
            await asyncio.sleep(0.02)
            state["now"] -= 1
            return "ok"

    vision, _ = resilient(Slow(), concurrency=2)
    await asyncio.gather(*(vision.analyze([IMG], "p") for _ in range(8)))
    assert state["peak"] == 2


# ----------------------------------------------------- request size guard (API)
def client(**kwargs) -> TestClient:
    s = Settings(gemini_api_key="k", _env_file=None, **kwargs)
    return TestClient(create_app(settings=s, listing_service=make_service(s, FakeVision(good_payload()))))


def test_declared_oversized_request_is_rejected_413_before_parsing():
    c = client(max_image_size_mb=1, max_images_per_request=1)  # limit = 2 MB
    r = c.post("/api/v1/generate-listing/", files=[("images", ("a.jpg", b"x" * (3 * 1024 * 1024), "image/jpeg"))])
    assert r.status_code == 413 and r.json()["error"]["code"] == "request_too_large"
    assert r.headers["x-request-id"] == r.json()["error"]["request_id"]


def test_normal_request_passes_size_guard():
    buf = io.BytesIO()
    Image.new("RGB", (100, 80), (1, 2, 3)).save(buf, format="JPEG")
    r = client().post("/api/v1/generate-listing/", files=[("images", ("a.jpg", buf.getvalue(), "image/jpeg"))])
    assert r.status_code == 200


# ------------------------------------------------------------ edge-case script
def test_every_edge_case_behaves_as_designed():
    from scripts.edge_cases import run_offline

    failures = [o.case.name for o in run_offline() if not o.passed]
    assert failures == []
