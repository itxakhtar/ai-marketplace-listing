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


def test_connection_refused_is_unavailable_with_helpful_message():
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(ApiUnavailable, match="uvicorn"):
        generate_listing("http://api", FILES, client=client_for(handler))


def test_timeout_is_unavailable():
    def handler(request):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(ApiUnavailable, match="too long"):
        generate_listing("http://api", FILES, client=client_for(handler))


def test_bad_url_scheme_rejected():
    with pytest.raises(ApiUnavailable, match="http"):
        generate_listing("localhost:8000", FILES, client=client_for(lambda r: httpx.Response(200, json=LISTING)))


def test_health_check_true_false():
    assert check_health("http://api", client=client_for(lambda r: httpx.Response(200, json={"status": "ok"})))
    assert not check_health("http://api", client=client_for(lambda r: httpx.Response(500)))

    def boom(request):
        raise httpx.ConnectError("x")

    assert not check_health("http://api", client=client_for(boom))
    assert not check_health("not-a-url")
