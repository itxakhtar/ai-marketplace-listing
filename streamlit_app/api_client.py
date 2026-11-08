"""Thin HTTP client for the FastAPI service. No Streamlit imports (unit-testable).

The UI talks to the API only; it never calls Gemini and never sees an API key.
"""

import httpx

DEFAULT_API_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT = 120.0  # seconds; vision calls take 5-20 s, retries can double it
GENERATE_PATH = "/api/v1/generate-listing/"

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
        raise ApiUnavailable("API URL must start with http:// or https://")
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


def generate_listing(
    base_url: str,
    files: list[UploadTuple],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.Client | None = None,
) -> dict:
    """POST images to the API; return the listing dict or raise ApiError/ApiUnavailable."""
    url = _base(base_url) + GENERATE_PATH
    multipart = [
        ("images", (name, data, content_type or "application/octet-stream"))
        for name, data, content_type in files
    ]
    owns_client = client is None
    http = client or httpx.Client(timeout=timeout)
    try:
        response = http.post(url, files=multipart)
    except httpx.ConnectError as exc:
        raise ApiUnavailable(
            f"Cannot reach the API at {base_url}. Is `uvicorn app.main:app` running?"
        ) from exc
    except httpx.TimeoutException as exc:
        raise ApiUnavailable("The API took too long to respond. Please try again.") from exc
    except httpx.HTTPError as exc:
        raise ApiUnavailable("Network error while contacting the API.") from exc
    finally:
        if owns_client:
            http.close()

    if response.status_code != 200:
        raise _error_from_response(response)
    try:
        body = response.json()
    except ValueError as exc:
        raise ApiError(200, "invalid_response", "The API returned a response that is not valid JSON.") from exc
    if not isinstance(body, dict):
        raise ApiError(200, "invalid_response", "The API returned an unexpected response shape.")
    return body


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
