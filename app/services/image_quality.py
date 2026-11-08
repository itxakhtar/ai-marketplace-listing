"""Pillow-only image quality heuristics (no numpy/OpenCV).

These are deliberately conservative and advisory. A clean studio shot of a plain
white product can look "flat" to an edge filter, so the only consequences of a
flag are: a cautious note in the prompt, and (when EVERY image is flagged)
forcing condition to "Unable to Determine". Nothing is ever rejected.
"""

from PIL import Image, ImageFilter, ImageStat

from app.core.config import Settings
from app.models.image import ImageQuality

_ANALYSIS_EDGE = 512  # measure at a fixed scale so the score is resolution-independent


def assess_quality(image: Image.Image, settings: Settings) -> ImageQuality:
    longest = max(image.size)
    gray = image.convert("L")
    if longest > _ANALYSIS_EDGE:
        gray = gray.copy()
        gray.thumbnail((_ANALYSIS_EDGE, _ANALYSIS_EDGE), Image.Resampling.BILINEAR)

    brightness = float(ImageStat.Stat(gray).mean[0])
    edges = gray.filter(ImageFilter.FIND_EDGES)
    # Pillow leaves the 1px border of a filtered image unfiltered (raw pixels), which
    # would inflate the variance, so measure the interior only.
    if min(edges.size) > 4:
        edges = edges.crop((2, 2, edges.width - 2, edges.height - 2))
    sharpness = float(ImageStat.Stat(edges).var[0])
    return ImageQuality(
        mean_brightness=round(brightness, 1),
        sharpness=round(sharpness, 1),
        longest_edge=longest,
        is_dark=brightness < settings.dark_threshold,
        is_blurry=sharpness < settings.blur_threshold,
        is_low_res=longest < settings.low_res_edge,
    )
