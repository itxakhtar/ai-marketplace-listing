"""Domain exceptions. Each carries a safe, client-facing message and HTTP status.

Provider-specific exceptions must never cross the service boundary; they are
translated into these types inside the service that owns the provider.
"""


class AppError(Exception):
    status_code: int = 500
    error_code: str = "internal_error"
    public_message: str = "An unexpected error occurred."

    def __init__(self, public_message: str | None = None) -> None:
        self.public_message = public_message or type(self).public_message
        super().__init__(self.public_message)


# --- Image errors (used from Day 2) -----------------------------------------
class InvalidImageError(AppError):
    status_code = 400
    error_code = "invalid_image"
    public_message = "The uploaded file is not a valid supported image."


class ImageTooLargeError(AppError):
    status_code = 413
    error_code = "image_too_large"
    public_message = "The uploaded image exceeds the allowed size."


# --- Vision provider errors ---------------------------------------------------
class VisionServiceError(AppError):
    status_code = 503
    error_code = "vision_service_error"
    public_message = "The vision service failed to process the request."


class VisionRateLimitError(VisionServiceError):
    status_code = 429
    error_code = "vision_rate_limited"
    public_message = "The vision service is rate limited. Please retry shortly."


class VisionUnavailableError(VisionServiceError):
    status_code = 503
    error_code = "vision_unavailable"
    public_message = "The vision service is temporarily unavailable."


class VisionTimeoutError(VisionServiceError):
    status_code = 503
    error_code = "vision_timeout"
    public_message = "The vision service timed out. Please retry."


class RequestTimeoutError(AppError):
    status_code = 503
    error_code = "request_timeout"
    public_message = "Generating the listing took too long. Please retry, ideally with fewer or smaller images."


class VisionEmptyResponseError(VisionServiceError):
    status_code = 503
    error_code = "vision_empty_response"
    public_message = "The vision service returned no usable response."


class VisionConfigurationError(VisionServiceError):
    """Bad/revoked API key or model name. Details are logged, never returned."""

    status_code = 503
    error_code = "vision_misconfigured"
    public_message = "The vision service is not configured correctly."


# --- AI output errors ----------------------------------------------------------
class NoProductDetectedError(AppError):
    status_code = 422
    error_code = "no_product_detected"
    public_message = "No identifiable product was found in the uploaded image(s)."


class ListingValidationError(AppError):
    status_code = 422
    error_code = "ai_output_invalid"
    public_message = "The AI response could not be validated against the listing schema."
