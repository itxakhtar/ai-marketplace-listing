"""Centralized application settings, loaded from environment / .env."""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Vision provider -------------------------------------------------
    gemini_api_key: SecretStr = Field(..., description="Google Gemini API key")
    gemini_model: str = "gemini-3.6-flash"
    gemini_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    # Gemini 2.5 "thinking" tokens count toward this limit, so keep it generous.
    gemini_max_output_tokens: int = Field(default=4096, ge=256)
    api_timeout: float = Field(default=60.0, gt=0, description="Seconds")
    # Hard ceiling for one whole /generate-listing call (all retries included).
    # Keep it below the Streamlit client's 120 s timeout.
    request_timeout: float = Field(default=110.0, gt=0, description="Seconds")

    # --- Image constraints ----------------------------------------------
    max_image_size_mb: int = Field(default=10, ge=1)
    max_image_width: int = Field(default=4096, ge=64)
    max_image_height: int = Field(default=4096, ge=64)
    max_images_per_request: int = Field(default=6, ge=1, le=10)
    # Longest edge (px) of the image actually sent to the vision model.
    inference_max_edge: int = Field(default=1568, ge=256)
    jpeg_quality: int = Field(default=85, ge=50, le=100)

    # --- Image quality heuristics (conservative; tune with scripts/edge_cases.py --folder) ---
    low_res_edge: int = Field(default=300, ge=64, description="Longest edge below this = low resolution")
    dark_threshold: float = Field(default=40.0, ge=0, le=255, description="Mean grey level below this = dark")
    blur_threshold: float = Field(default=12.0, ge=0, description="Edge variance below this = blurry")

    # --- Vision provider resilience ---------------------------------------------
    vision_max_retries: int = Field(default=1, ge=0, le=3)  # transient errors only
    vision_retry_backoff_s: float = Field(default=1.0, ge=0)
    vision_max_concurrency: int = Field(default=4, ge=1, le=32)

    # --- AI output handling -------------------------------------------------
    # Total attempts when the model output fails parsing/validation/guardrails.
    max_ai_attempts: int = Field(default=2, ge=1, le=3)
    # Reject listings that state storage / RAM / battery numbers (almost always invented).
    block_unverified_specs: bool = True

    # --- Logging ---------------------------------------------------------
    log_level: str = "INFO"

    @property
    def max_request_bytes(self) -> int:
        """Upper bound for a whole multipart request (all files + 1 MB of overhead)."""
        return self.max_images_per_request * self.max_image_size_bytes + 1024 * 1024

    @property
    def max_image_size_bytes(self) -> int:
        return self.max_image_size_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor; also usable as a FastAPI dependency."""
    return Settings()  # type: ignore[call-arg]
