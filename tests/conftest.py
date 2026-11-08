import pytest

from app.core.config import Settings


@pytest.fixture
def settings() -> Settings:
    # _env_file=None keeps tests independent of a developer's real .env
    return Settings(gemini_api_key="test-key", _env_file=None)
