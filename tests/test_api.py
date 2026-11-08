import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.exceptions import VisionRateLimitError, VisionUnavailableError
from app.main import create_app
from app.services.image_service import ImageService
from app.services.listing_service import ListingService
from tests.helpers import FakeVision, good_payload, jpeg_bytes

URL = "/api/v1/generate-listing/"


def build_client(vision: FakeVision, **settings_kwargs) -> TestClient:
    settings = Settings(gemini_api_key="test-key", _env_file=None, **settings_kwargs)
    service = ListingService(ImageService(settings), vision, settings)
    return TestClient(create_app(settings=settings, listing_service=service))


def files(*contents: bytes, content_type="image/jpeg", name="images"):
    return [(name, (f"p{i}.jpg", data, content_type)) for i, data in enumerate(contents)]


def error_code(response) -> str:
    return response.json()["error"]["code"]


# ------------------------------------------------------------------ success
def test_generate_listing_success():
    client = build_client(FakeVision(good_payload()))
    r = client.post(URL, files=files(jpeg_bytes()))
    assert r.status_code == 200
    body = r.json()
    assert body["category"] == "Mobiles and Tablets"
    assert body["condition_estimate"] == "Unable to Determine"
    assert len(body["suggested_title"]) <= 70
    assert 5 <= len(body["tags"]) <= 8


def test_multiple_images_become_one_listing_and_reach_vision_together():
    vision = FakeVision(good_payload())
    r = build_client(vision).post(URL, files=files(jpeg_bytes(), jpeg_bytes(), jpeg_bytes()))
    assert r.status_code == 200
    assert len(vision.calls) == 1 and len(vision.calls[0][0]) == 3


def test_png_and_webp_accepted():
    import io

    from PIL import Image

    def enc(fmt):
        b = io.BytesIO()
        Image.new("RGB", (80, 80), (1, 2, 3)).save(b, format=fmt)
        return b.getvalue()

    vision = FakeVision(good_payload())
    r = build_client(vision).post(
        URL,
        files=[
            ("images", ("a.png", enc("PNG"), "image/png")),
            ("images", ("b.webp", enc("WEBP"), "image/webp")),
        ],
    )
    assert r.status_code == 200


def test_health():
    r = build_client(FakeVision()).get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_swagger_and_redoc_and_openapi_available():
    client = build_client(FakeVision())
    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200
    schema = client.get("/openapi.json").json()
    assert "/api/v1/generate-listing/" in schema["paths"]
    responses = schema["paths"]["/api/v1/generate-listing/"]["post"]["responses"]
    assert {"200", "400", "413", "422", "429", "500", "503"} <= set(responses)


# ---------------------------------------------------------------- request id
def test_request_id_generated_and_returned():
    r = build_client(FakeVision(good_payload())).post(URL, files=files(jpeg_bytes()))
    assert len(r.headers["x-request-id"]) == 32


def test_valid_client_request_id_is_echoed():
    r = build_client(FakeVision()).get("/health", headers={"X-Request-ID": "abc-123"})
    assert r.headers["x-request-id"] == "abc-123"


def test_unsafe_client_request_id_is_replaced():
    r = build_client(FakeVision()).get("/health", headers={"X-Request-ID": "bad id; drop table"})
    assert r.headers["x-request-id"] != "bad id; drop table"


def test_error_body_contains_matching_request_id():
    r = build_client(FakeVision()).post(URL, files=files(b"nope"), headers={"X-Request-ID": "rid-1"})
    assert r.json()["error"]["request_id"] == "rid-1"


# ------------------------------------------------------------- 400 / 413 input
def test_no_files_is_400():
    r = build_client(FakeVision()).post(URL)
    assert r.status_code == 400 and error_code(r) == "invalid_request"


def test_wrong_field_name_is_400():
    r = build_client(FakeVision()).post(URL, files=files(jpeg_bytes(), name="photo"))
    assert r.status_code == 400


def test_corrupt_image_is_400_and_never_reaches_vision():
    vision = FakeVision(good_payload())
    r = build_client(vision).post(URL, files=files(b"not an image"))
    assert r.status_code == 400 and error_code(r) == "invalid_image"
    assert vision.calls == []


def test_empty_file_is_400():
    r = build_client(FakeVision()).post(URL, files=files(b""))
    assert r.status_code == 400 and error_code(r) == "invalid_image"


def test_unsupported_mime_is_400():
    r = build_client(FakeVision()).post(URL, files=files(jpeg_bytes(), content_type="application/pdf"))
    assert r.status_code == 400 and error_code(r) == "invalid_image"


def test_second_bad_image_is_identified_by_position():
    r = build_client(FakeVision(good_payload())).post(URL, files=files(jpeg_bytes(), b"junk"))
    assert r.status_code == 400 and "Image 2" in r.json()["error"]["message"]


def test_too_many_images_is_400():
    client = build_client(FakeVision(good_payload()), max_images_per_request=2)
    r = client.post(URL, files=files(jpeg_bytes(), jpeg_bytes(), jpeg_bytes()))
    assert r.status_code == 400 and "Too many" in r.json()["error"]["message"]


def test_oversized_file_is_413():
    client = build_client(FakeVision(good_payload()), max_image_size_mb=1)
    r = client.post(URL, files=files(b"x" * (1024 * 1024 + 10)))
    assert r.status_code == 413 and error_code(r) == "image_too_large"


def test_oversized_dimensions_are_400():
    from tests.helpers import jpeg_bytes as jb

    r = build_client(FakeVision(good_payload())).post(URL, files=files(jb(size=(4100, 40))))
    assert r.status_code == 400 and "exceed" in r.json()["error"]["message"]


# ------------------------------------------------------------------ AI errors
def test_non_product_image_is_422():
    r = build_client(FakeVision({"error": "no_identifiable_product"})).post(URL, files=files(jpeg_bytes()))
    assert r.status_code == 422 and error_code(r) == "no_product_detected"


def test_unvalidatable_ai_output_is_422_and_malformed_output_never_returned():
    vision = FakeVision("totally not json {{", "still not json")
    r = build_client(vision).post(URL, files=files(jpeg_bytes()))
    assert r.status_code == 422 and error_code(r) == "ai_output_invalid"
    assert "not json" not in r.text


def test_ai_output_with_forbidden_claim_is_422():
    bad = good_payload(suggested_description="Fully functional and under warranty.")
    r = build_client(FakeVision(bad, bad)).post(URL, files=files(jpeg_bytes()))
    assert r.status_code == 422


# ------------------------------------------------------------ provider errors
def test_rate_limit_is_429_with_retry_after():
    r = build_client(FakeVision(VisionRateLimitError())).post(URL, files=files(jpeg_bytes()))
    assert r.status_code == 429 and error_code(r) == "vision_rate_limited"
    assert r.headers["retry-after"] == "30"


def test_provider_unavailable_is_503():
    r = build_client(FakeVision(VisionUnavailableError())).post(URL, files=files(jpeg_bytes()))
    assert r.status_code == 503 and error_code(r) == "vision_unavailable"


def test_unexpected_error_is_500_without_leaking_details():
    secret = "SECRET-KEY-abc123 /internal/path"
    r = build_client(FakeVision(RuntimeError(secret))).post(URL, files=files(jpeg_bytes()))
    assert r.status_code == 500 and error_code(r) == "internal_error"
    assert "SECRET" not in r.text and "internal/path" not in r.text and "Traceback" not in r.text
    assert "x-request-id" in r.headers


# ------------------------------------------------------------------- generic
def test_unknown_route_uses_error_envelope():
    r = build_client(FakeVision()).get("/nope")
    assert r.status_code == 404 and error_code(r) == "not_found"


def test_wrong_method_uses_error_envelope():
    r = build_client(FakeVision()).get(URL)
    assert r.status_code == 405 and error_code(r) == "method_not_allowed"
