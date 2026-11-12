import json

import httpx
import pytest

from streamlit_app.api_client import (
    ApiError,
    ApiUnavailable,
    check_health,
    generate_listing,
)

LISTING = {"detected_item": "Tablet", "tags": ["a", "b", "c", "d", "e"]}
FILES = [("a.jpg", b"\xff\xd8data", "image/jpeg")]


def client_for(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_success_returns_listing_and_sends_multipart_images_field():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.read()
        return httpx.Response(200, json=LISTING)

    result = generate_listing("http://api:8000/", FILES + FILES, client=client_for(handler))
    assert result == LISTING
    assert seen["url"] == "http://api:8000/api/v1/generate-listing/"
    assert seen["body"].count(b'name="images"') == 2
    assert b"multipart/form-data" in httpx.Request("POST", "http://x", files=[("images", ("a", b"b"))]).headers["content-type"].encode()


def test_missing_content_type_falls_back_to_octet_stream():
    seen = {}

    def handler(request):
        seen["body"] = request.read()
        return httpx.Response(200, json=LISTING)

    generate_listing("http://api", [("a.jfif", b"x", None)], client=client_for(handler))
    assert b"application/octet-stream" in seen["body"]


def test_error_envelope_is_parsed():
    body = {"error": {"code": "invalid_image", "message": "Image 1: bad", "request_id": "rid9"}}
    with pytest.raises(ApiError) as info:
        generate_listing("http://api", FILES, client=client_for(lambda r: httpx.Response(400, json=body)))
    e = info.value
    assert (e.status_code, e.code, e.message, e.request_id) == (400, "invalid_image", "Image 1: bad", "rid9")


def test_non_json_error_body_is_handled():
    with pytest.raises(ApiError) as info:
        generate_listing("http://api", FILES, client=client_for(lambda r: httpx.Response(502, text="<html>Bad gateway</html>")))
    assert info.value.status_code == 502 and "502" in info.value.message


def test_200_with_invalid_json_is_an_error():
    with pytest.raises(ApiError) as info:
        generate_listing("http://api", FILES, client=client_for(lambda r: httpx.Response(200, text="oops")))
    assert info.value.code == "invalid_response"


def test_200_with_non_object_json_is_an_error():
    with pytest.raises(ApiError):
        generate_listing("http://api", FILES, client=client_for(lambda r: httpx.Response(200, json=[1, 2])))


def test_connection_refused_message_never_reveals_the_address():
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(ApiUnavailable) as info:
        generate_listing("http://secret-host.internal:8000", FILES, client=client_for(handler))
    assert "can't reach" in str(info.value)
    assert "secret-host" not in str(info.value) and "http" not in str(info.value)


def test_timeout_is_unavailable():
    def handler(request):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(ApiUnavailable, match="too long"):
        generate_listing("http://api", FILES, client=client_for(handler))


def test_bad_url_scheme_rejected():
    with pytest.raises(ApiUnavailable, match="not configured"):
        generate_listing("localhost:8000", FILES, client=client_for(lambda r: httpx.Response(200, json=LISTING)))


def test_health_check_true_false():
    assert check_health("http://api", client=client_for(lambda r: httpx.Response(200, json={"status": "ok"})))
    assert not check_health("http://api", client=client_for(lambda r: httpx.Response(500)))

    def boom(request):
        raise httpx.ConnectError("x")

    assert not check_health("http://api", client=client_for(boom))
    assert not check_health("not-a-url")


# ---------------------------------------------------------- expand + images
from streamlit_app.api_client import expand_description, generate_images  # noqa: E402


def test_expand_description_sends_only_listing_fields_as_json():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["path"] = request.url.path
        return httpx.Response(200, json={"long_description": "A long text"})

    listing = {**LISTING, "suggested_title": "Tablet - Silver", "junk": "dropped"}
    assert expand_description("http://api", listing, client=client_for(handler)) == "A long text"
    assert seen["path"] == "/api/v1/expand-description/"
    assert "junk" not in seen["body"] and seen["body"]["suggested_title"] == "Tablet - Silver"


def test_expand_description_errors():
    with pytest.raises(ApiError) as info:
        expand_description(
            "http://api",
            LISTING,
            client=client_for(lambda r: httpx.Response(422, json={"error": {"code": "ai_output_invalid", "message": "bad", "request_id": "r"}})),
        )
    assert info.value.code == "ai_output_invalid"
    with pytest.raises(ApiError):
        expand_description("http://api", LISTING, client=client_for(lambda r: httpx.Response(200, json={"long_description": " "})))


def test_generate_images_posts_files_and_item_name():
    seen = {}

    def handler(request):
        seen["body"] = request.content
        seen["path"] = request.url.path
        return httpx.Response(200, json={"images": [{"label": "Studio view", "data_base64": "QUJD"}, {"label": "x"}]})

    out = generate_images("http://api", FILES, "Samsung Tablet", client=client_for(handler))
    assert seen["path"] == "/api/v1/generate-images/" and b"Samsung Tablet" in seen["body"]
    assert out == [{"label": "Studio view", "data_base64": "QUJD"}]  # entries without data are dropped


def test_generate_images_empty_is_an_error():
    with pytest.raises(ApiError):
        generate_images("http://api", FILES, client=client_for(lambda r: httpx.Response(200, json={"images": []})))
