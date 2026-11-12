"""Thin HTTP client for the FastAPI service. No Streamlit imports (unit-testable).

The UI talks to the API only; it never calls Gemini and never sees an API key.
"""

import httpx

DEFAULT_API_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT = 120.0  # seconds; vision calls take 5-20 s, retries can double it
GENERATE_PATH = "/api/v1/generate-listing/"
EXPAND_PATH = "/api/v1/expand-description/"
IMAGES_PATH = "/api/v1/generate-images/"
IMAGES_TIMEOUT = 150.0  # image generation is the slowest step

# User-facing text. Deliberately contains no URL, host name or technical detail.
MSG_UNREACHABLE = "We can't reach the listing service right now. Please try again in a moment."
MSG_SLOW = "The service took too long to respond. Please try again."
MSG_NETWORK = "A network problem interrupted the request. Please try again."
MSG_MISCONFIGURED = "The service address is not configured correctly."

# (filename, raw bytes, content type)
UploadTuple = tuple[str, bytes, str | None]


class ApiError(Exception):
    """The API answered with an error envelope (or an unusable body)."""

    def __init__(self, status_code: int, code: str, message: str, request_id: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.request_id = request_id


class ApiUnavailable(Exception):
    """The API could not be reached or did not answer in time."""


def _base(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise ApiUnavailable(MSG_MISCONFIGURED)
    return url


def _error_from_response(response: httpx.Response) -> ApiError:
    try:
        err = response.json()["error"]
        return ApiError(
            response.status_code,
            str(err.get("code", "http_error")),
            str(err.get("message", f"HTTP {response.status_code}")),
            err.get("request_id"),
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        return ApiError(response.status_code, "http_error", f"The API returned HTTP {response.status_code}.")


def _send(
    base_url: str,
    path: str,
    *,
    timeout: float,
    client: httpx.Client | None,
    **request_kwargs,
) -> dict:
    """POST to the API and return the JSON object, translating every failure."""
    url = _base(base_url) + path
    owns_client = client is None
    http = client or httpx.Client(timeout=timeout)
    try:
        response = http.post(url, **request_kwargs)
    except httpx.ConnectError as exc:
        raise ApiUnavailable(MSG_UNREACHABLE) from exc
    except httpx.TimeoutException as exc:
        raise ApiUnavailable(MSG_SLOW) from exc
    except httpx.HTTPError as exc:
        raise ApiUnavailable(MSG_NETWORK) from exc
    finally:
        if owns_client:
            http.close()

    if response.status_code != 200:
        raise _error_from_response(response)
    try:
        body = response.json()
    except ValueError as exc:
        raise ApiError(200, "invalid_response", "The service returned a response that is not valid JSON.") from exc
    if not isinstance(body, dict):
        raise ApiError(200, "invalid_response", "The service returned an unexpected response shape.")
    return body


def _multipart(files: list[UploadTuple]) -> list:
    return [
        ("images", (name, data, content_type or "application/octet-stream"))
        for name, data, content_type in files
    ]


def generate_listing(
    base_url: str,
    files: list[UploadTuple],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.Client | None = None,
) -> dict:
    """POST images to the API; return the listing dict or raise ApiError/ApiUnavailable."""
    return _send(base_url, GENERATE_PATH, timeout=timeout, client=client, files=_multipart(files))


def expand_description(
    base_url: str,
    listing: dict,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.Client | None = None,
) -> str:
    """Ask the API for the long description of a (possibly edited) listing."""
    fields = (
        "detected_item",
        "category",
        "condition_estimate",
        "suggested_title",
        "suggested_description",
        "extracted_features",
        "tags",
    )
    payload = {k: listing[k] for k in fields if k in listing}
    body = _send(base_url, EXPAND_PATH, timeout=timeout, client=client, json=payload)
    text = body.get("long_description")
    if not isinstance(text, str) or not text.strip():
        raise ApiError(200, "invalid_response", "The service returned an empty description.")
    return text


def generate_images(
    base_url: str,
    files: list[UploadTuple],
    detected_item: str | None = None,
    *,
    timeout: float = IMAGES_TIMEOUT,
    client: httpx.Client | None = None,
) -> list[dict]:
    """Return the AI-generated related pictures: [{label, mime_type, data_base64, ai_generated}]."""
    data = {"detected_item": detected_item} if detected_item else None
    body = _send(base_url, IMAGES_PATH, timeout=timeout, client=client, files=_multipart(files), data=data)
    images = body.get("images")
    if not isinstance(images, list) or not images:
        raise ApiError(200, "invalid_response", "The service returned no images.")
    return [i for i in images if isinstance(i, dict) and i.get("data_base64")]


def check_health(base_url: str, *, timeout: float = 5.0, client: httpx.Client | None = None) -> bool:
    try:
        url = _base(base_url) + "/health"
    except ApiUnavailable:
        return False
    owns_client = client is None
    http = client or httpx.Client(timeout=timeout)
    try:
        return http.get(url).status_code == 200
    except httpx.HTTPError:
        return False
    finally:
        if owns_client:
            http.close()
