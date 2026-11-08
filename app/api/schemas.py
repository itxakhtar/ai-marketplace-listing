from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    code: str = Field(..., examples=["invalid_image"])
    message: str = Field(..., examples=["Image 2: The uploaded file is not a readable image."])
    request_id: str = Field(..., examples=["3f9c1a7e5b2d4c60"])


class ErrorResponse(BaseModel):
    error: ErrorBody


class HealthResponse(BaseModel):
    status: str = "ok"
