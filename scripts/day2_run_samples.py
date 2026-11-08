"""Run the full ListingService over every image in a folder and print a summary.

Usage (from the project root):
    python -m scripts.day2_run_samples                      # one listing per image
    python -m scripts.day2_run_samples --group              # all images = one product
    python -m scripts.day2_run_samples --dir sample_images --json results.json
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.logging import configure_logging
from app.models.image import UploadedImage
from app.services.image_service import ImageService
from app.services.listing_service import ListingService
from app.services.vision_service import VisionService

SUFFIXES = {".jpg", ".jpeg", ".jfif", ".png", ".webp"}


def _upload(path: Path) -> UploadedImage:
    return UploadedImage(data=path.read_bytes(), filename=path.name)


async def run_one(service: ListingService, label: str, files: list[Path]) -> dict:
    try:
        listing = await service.generate([_upload(p) for p in files])
    except AppError as exc:
        return {"input": label, "ok": False, "error": f"{exc.error_code}: {exc.public_message}"}
    return {"input": label, "ok": True, "listing": listing.model_dump(mode="json")}


def print_table(results: list[dict]) -> None:
    print(f"\n{'INPUT':34} {'CATEGORY':22} {'CONDITION':22} {'TITLE':>5}  RESULT")
    print("-" * 110)
    for r in results:
        if r["ok"]:
            item = r["listing"]
            print(
                f"{r['input'][:33]:34} {item['category']:22} {item['condition_estimate']:22} "
                f"{len(item['suggested_title']):>5}  OK  ({item['detected_item']})"
            )
        else:
            print(f"{r['input'][:33]:34} {'-':22} {'-':22} {'-':>5}  FAIL  {r['error']}")


async def main(folder: Path, group: bool, json_out: Path | None) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in SUFFIXES)
    if not files:
        sys.exit(f"No images found in {folder}")

    service = ListingService(ImageService(settings), VisionService(settings), settings)
    if group:
        results = [await run_one(service, f"{len(files)} images (one product)", files)]
    else:
        results = [await run_one(service, p.name, [p]) for p in files]

    print_table(results)
    if json_out:
        json_out.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"\nFull results written to {json_out}")
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=Path("sample_images"))
    parser.add_argument("--group", action="store_true")
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.dir, args.group, args.json)))
