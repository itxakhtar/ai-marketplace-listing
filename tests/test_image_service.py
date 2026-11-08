import io

import pytest
from PIL import Image

from app.core.config import Settings
from app.core.exceptions import ImageTooLargeError, InvalidImageError
from app.models.image import UploadedImage
from app.services.image_service import ImageService


def make_bytes(fmt="JPEG", size=(200, 100), mode="RGB", color=(200, 30, 30), **save_kwargs) -> bytes:
    img = Image.new(mode, size, color)
    buf = io.BytesIO()
    img.save(buf, format=fmt, **save_kwargs)
    return buf.getvalue()


@pytest.fixture
def service(settings: Settings) -> ImageService:
    return ImageService(settings)


def decode(payload) -> Image.Image:
    return Image.open(io.BytesIO(payload.data))


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_supported_formats_are_normalized_to_rgb_jpeg(service, fmt):
    payload = service.process(UploadedImage(make_bytes(fmt)))
    img = decode(payload)
    assert payload.mime_type == "image/jpeg"
    assert img.format == "JPEG" and img.mode == "RGB"


def test_empty_file_rejected(service):
    with pytest.raises(InvalidImageError):
        service.process(UploadedImage(b""))


def test_oversized_file_rejected_with_413_error(settings):
    small = Settings(gemini_api_key="k", max_image_size_mb=1, _env_file=None)
    with pytest.raises(ImageTooLargeError):
        ImageService(small).process(UploadedImage(b"x" * (1024 * 1024 + 1)))


def test_non_image_bytes_rejected(service):
    with pytest.raises(InvalidImageError):
        service.process(UploadedImage(b"%PDF-1.4 definitely not an image"))


def test_corrupted_png_rejected(service):
    data = make_bytes("PNG", size=(300, 300))
    with pytest.raises(InvalidImageError):
        service.process(UploadedImage(data[: len(data) // 2]))


def test_gif_rejected_even_though_pillow_can_read_it(service):
    with pytest.raises(InvalidImageError):
        service.process(UploadedImage(make_bytes("GIF", mode="P", color=1)))


def test_wrong_declared_mime_rejected(service):
    with pytest.raises(InvalidImageError):
        service.process(UploadedImage(make_bytes("JPEG"), content_type="application/pdf"))


def test_lying_but_allowed_mime_is_accepted_based_on_real_content(service):
    payload = service.process(UploadedImage(make_bytes("PNG"), content_type="image/jpeg"))
    assert decode(payload).format == "JPEG"


def test_octet_stream_mime_is_accepted_if_content_is_valid(service):
    assert service.process(UploadedImage(make_bytes("JPEG"), content_type="application/octet-stream"))


def test_dimensions_over_limit_rejected(service):
    with pytest.raises(InvalidImageError, match="exceed"):
        service.process(UploadedImage(make_bytes("PNG", size=(4097, 40))))


def test_tiny_image_rejected(service):
    with pytest.raises(InvalidImageError, match="too small"):
        service.process(UploadedImage(make_bytes("PNG", size=(10, 10))))


def test_large_image_is_downscaled_preserving_aspect_ratio(service, settings):
    payload = service.process(UploadedImage(make_bytes("JPEG", size=(3000, 2000))))
    w, h = decode(payload).size
    assert max(w, h) == settings.inference_max_edge
    assert abs((w / h) - 1.5) < 0.01


def test_small_image_is_not_upscaled(service):
    payload = service.process(UploadedImage(make_bytes("JPEG", size=(300, 200))))
    assert decode(payload).size == (300, 200)


def test_transparency_is_flattened_onto_white(service):
    data = make_bytes("PNG", size=(100, 100), mode="RGBA", color=(0, 0, 0, 0))
    img = decode(service.process(UploadedImage(data)))
    r, g, b = img.getpixel((50, 50))
    assert min(r, g, b) > 240


def test_exif_orientation_is_applied(service):
    img = Image.new("RGB", (100, 50), (10, 200, 10))
    exif = img.getexif()
    exif[0x0112] = 6  # rotate 90 degrees
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif.tobytes())
    assert decode(service.process(UploadedImage(buf.getvalue()))).size == (50, 100)
