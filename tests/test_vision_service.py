"""VisionService tests with a fake Gemini client; no network access."""

from types import SimpleNamespace

import pytest
from google.genai import errors as genai_errors

from app.core.config import Settings
from app.core.exceptions import (
    VisionConfigurationError,
    VisionEmptyResponseError,
    VisionRateLimitError,
    VisionUnavailableError,
)
from app.models.image import ImagePayload
from app.services.vision_service import VisionService

IMG = ImagePayload(data=b"\xff\xd8fake", mime_type="image/jpeg")


class FakeModels:
    def __init__(self, result=None, error=None):
        self.result, self.error = result, error

    async def generate_content(self, **kwargs):
        if self.error:
            raise self.error
        return self.result


def make_service(result=None, error=None) -> VisionService:
    settings = Settings(gemini_api_key="test-key", _env_file=None)
    client = SimpleNamespace(aio=SimpleNamespace(models=FakeModels(result, error)))
    return VisionService(settings, client=client)  # type: ignore[arg-type]


def api_error(code: int) -> genai_errors.APIError:
    return genai_errors.APIError(code, {"error": {"message": "x", "status": "X"}})


async def test_returns_text():
    svc = make_service(result=SimpleNamespace(text='  {"a": 1} '))
    assert await svc.analyze([IMG], "p") == '{"a": 1}'


async def test_empty_response_raises():
    svc = make_service(result=SimpleNamespace(text=None))
    with pytest.raises(VisionEmptyResponseError):
        await svc.analyze([IMG], "p")


@pytest.mark.parametrize(
    "code,expected",
    [(429, VisionRateLimitError), (403, VisionConfigurationError), (503, VisionUnavailableError)],
)
async def test_provider_errors_are_translated(code, expected):
    svc = make_service(error=api_error(code))
    with pytest.raises(expected):
        await svc.analyze([IMG], "p")


async def test_no_images_rejected():
    svc = make_service(result=SimpleNamespace(text="x"))
    with pytest.raises(ValueError):
        await svc.analyze([], "p")


class ReadTimeout(Exception):
    """Mimics an HTTP-library timeout (class name is what we classify on)."""


class ConnectError(Exception):
    pass


async def test_library_timeout_classified_by_name():
    from app.core.exceptions import VisionTimeoutError

    svc = make_service(error=ReadTimeout())
    with pytest.raises(VisionTimeoutError):
        await svc.analyze([IMG], "p")


async def test_connection_failure_is_unavailable():
    svc = make_service(error=ConnectError())
    with pytest.raises(VisionUnavailableError):
        await svc.analyze([IMG], "p")


async def test_unknown_exception_becomes_generic_service_error():
    from app.core.exceptions import VisionServiceError

    svc = make_service(error=RuntimeError("boom"))
    with pytest.raises(VisionServiceError) as info:
        await svc.analyze([IMG], "p")
    assert "boom" not in info.value.public_message


async def test_hard_deadline_when_provider_hangs(monkeypatch):
    import asyncio

    from app.core.exceptions import VisionTimeoutError

    class Hanging:
        async def generate_content(self, **kwargs):
            await asyncio.sleep(10)

    settings = Settings(gemini_api_key="k", api_timeout=0.05, _env_file=None)
    client = SimpleNamespace(aio=SimpleNamespace(models=Hanging()))
    svc = VisionService(settings, client=client)  # type: ignore[arg-type]
    monkeypatch.setattr(VisionService, "_timeout_grace_s", 0.0)
    with pytest.raises(VisionTimeoutError):
        await svc.analyze([IMG], "p")
