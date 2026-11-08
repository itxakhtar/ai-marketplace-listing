"""Baseline: send local image(s) to Gemini and print the raw response.

Images go through ImageService first, so .jfif/.png/.webp all work.

Usage (from the project root):
    python -m scripts.day1_baseline sample_images/phone.jpg
    python -m scripts.day1_baseline front.jpg back.jpg
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.logging import configure_logging
from app.models.image import UploadedImage
from app.models.listing import ListingResponse
from app.prompts.listing_prompt import build_listing_prompt
from app.services.image_service import ImageService
from app.services.vision_service import VisionService


async def main(paths: list[Path]) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)

    images = []
    for path in paths:
        if not path.is_file():
            sys.exit(f"File not found: {path}")
        try:
            images.append(ImageService(settings).process(UploadedImage(path.read_bytes(), path.name)))
        except AppError as exc:
            sys.exit(f"{path.name}: [{exc.error_code}] {exc.public_message}")

    try:
        raw = await VisionService(settings).analyze(images, build_listing_prompt(len(images)))
    except AppError as exc:
        print(f"[{exc.error_code}] HTTP {exc.status_code}: {exc.public_message}")
        return 1

    print("=== RAW MODEL RESPONSE ===")
    print(raw)
    print("\n=== SCHEMA CHECK (informational) ===")
    try:
        print(ListingResponse.model_validate(json.loads(raw)).model_dump_json(indent=2))
    except (json.JSONDecodeError, ValidationError) as exc:
        print(f"Not valid against schema: {type(exc).__name__}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="+", type=Path)
    sys.exit(asyncio.run(main(parser.parse_args().images)))
