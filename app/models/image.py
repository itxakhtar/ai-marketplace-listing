from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class UploadedImage:
    """Raw upload as received from the client. Nothing here is trusted."""

    data: bytes
    filename: str | None = None
    content_type: str | None = None


@dataclass(frozen=True, slots=True)
class ImageQuality:
    """Cheap, Pillow-only heuristics. Advisory: they never reject an image."""

    mean_brightness: float  # 0 (black) .. 255 (white)
    sharpness: float  # variance of an edge-filtered thumbnail; low = blurry/flat
    longest_edge: int
    is_dark: bool
    is_blurry: bool
    is_low_res: bool

    @property
    def is_poor(self) -> bool:
        return self.is_dark or self.is_blurry or self.is_low_res

    @property
    def problems(self) -> list[str]:
        found = []
        if self.is_dark:
            found.append("very dark")
        if self.is_blurry:
            found.append("blurry or lacking fine detail")
        if self.is_low_res:
            found.append("very low resolution")
        return found


@dataclass(frozen=True, slots=True)
class ImagePayload:
    """Validated, normalized image ready for the vision provider."""

    data: bytes
    mime_type: str  # always "image/jpeg" after normalization
    quality: ImageQuality | None = None
