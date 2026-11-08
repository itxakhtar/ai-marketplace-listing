"""Image validation and normalization with Pillow.

Pipeline: size -> declared MIME -> decode/format -> dimensions -> full decode
(catches truncation) -> EXIF rotation -> RGB -> resize -> JPEG.

The file's real content is the source of truth; the declared MIME type and file
extension are only used to reject obviously wrong uploads early.
"""

import io
import logging

from PIL import Image, ImageOps

from app.core.config import Settings
from app.core.exceptions import ImageTooLargeError, InvalidImageError
from app.models.image import ImagePayload, UploadedImage
from app.services.image_quality import assess_quality

logger = logging.getLogger(__name__)

ALLOWED_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})  # .jfif files are JPEG
ALLOWED_MIME = frozenset({"image/jpeg", "image/pjpeg", "image/png", "image/webp"})
MIN_EDGE_PX = 32


class ImageService:
    def __init__(self, settings: Settings) -> None:
        self._s = settings

    def process(self, upload: UploadedImage) -> ImagePayload:
        data = upload.data
        if not data:
            raise InvalidImageError("The uploaded file is empty.")
        if len(data) > self._s.max_image_size_bytes:
            raise ImageTooLargeError(f"Image exceeds the {self._s.max_image_size_mb} MB size limit.")
        self._check_declared_mime(upload.content_type)

        try:
            probe = Image.open(io.BytesIO(data))
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            # UnidentifiedImageError is an OSError subclass.
            raise InvalidImageError("The uploaded file is not a readable image.") from exc

        with probe:
            fmt = probe.format
            width, height = probe.size
            self._check_format(fmt)
            self._check_dimensions(width, height)
            try:
                probe.load()  # full decode; raises on truncated/corrupt data
            except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
                raise InvalidImageError("The image file is corrupted or truncated.") from exc

            image = ImageOps.exif_transpose(probe)
            image = self._to_rgb(image)
            quality = assess_quality(image, self._s)
            resized = self._resize(image)
            encoded = self._encode_jpeg(resized)
            out_size = resized.size

        logger.info(
            "image_processed",
            extra={
                "source_format": fmt,
                "source_size": f"{width}x{height}",
                "output_size": f"{out_size[0]}x{out_size[1]}",
                "input_bytes": len(data),
                "output_bytes": len(encoded),
                "quality_flags": ",".join(quality.problems) or "none",
            },
        )
        return ImagePayload(data=encoded, mime_type="image/jpeg", quality=quality)

    # ------------------------------------------------------------------ checks
    @staticmethod
    def _check_declared_mime(content_type: str | None) -> None:
        if not content_type:
            return
        mime = content_type.split(";")[0].strip().lower()
        if mime in ALLOWED_MIME or mime in ("application/octet-stream", ""):
            return  # real format is verified from the bytes below
        raise InvalidImageError("Unsupported file type. Upload a JPEG, PNG or WEBP image.")

    @staticmethod
    def _check_format(fmt: str | None) -> None:
        if fmt not in ALLOWED_FORMATS:
            raise InvalidImageError("Unsupported image format. Use JPEG, PNG or WEBP.")

    def _check_dimensions(self, width: int, height: int) -> None:
        if width < MIN_EDGE_PX or height < MIN_EDGE_PX:
            raise InvalidImageError(f"Image is too small ({width}x{height}px).")
        if width > self._s.max_image_width or height > self._s.max_image_height:
            raise InvalidImageError(
                f"Image dimensions {width}x{height}px exceed the "
                f"{self._s.max_image_width}x{self._s.max_image_height}px limit."
            )

    # ----------------------------------------------------------- transformations
    @staticmethod
    def _to_rgb(image: Image.Image) -> Image.Image:
        has_alpha = image.mode in ("RGBA", "LA") or (
            image.mode == "P" and "transparency" in image.info
        )
        if has_alpha:
            rgba = image.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.getchannel("A"))
            return background
        return image.convert("RGB") if image.mode != "RGB" else image

    def _resize(self, image: Image.Image) -> Image.Image:
        edge = self._s.inference_max_edge
        if max(image.size) <= edge:
            return image  # never upscale
        resized = image.copy()
        resized.thumbnail((edge, edge), Image.Resampling.LANCZOS)  # keeps aspect ratio
        return resized

    def _encode_jpeg(self, image: Image.Image) -> bytes:
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=self._s.jpeg_quality, optimize=True)
        return buffer.getvalue()
