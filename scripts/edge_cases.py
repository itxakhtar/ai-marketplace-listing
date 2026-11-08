"""Run awkward inputs through ImageService (offline, no API key needed).

Generates synthetic bad/odd images in memory and checks each is handled as designed:
accepted (with quality flags) or rejected with the right error code.

Usage (project root):
    python -m scripts.edge_cases                    # offline check + table
    python -m scripts.edge_cases --md evaluation/EDGE_CASES.md
    python -m scripts.edge_cases --save edge_images # also write the files to disk
    python -m scripts.edge_cases --folder sample_images   # print quality numbers for YOUR photos
    python -m scripts.edge_cases --with-model       # also send accepted cases to Gemini
"""

import argparse
import asyncio
import io
import os
import random
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from app.core.config import Settings, get_settings
from app.core.exceptions import AppError
from app.models.image import UploadedImage
from app.services.image_service import ImageService


@dataclass
class Case:
    name: str
    data: bytes
    content_type: str
    expect_error: str | None  # error_code, or None if it must be accepted
    expect_flags: frozenset[str] = frozenset()  # subset of {"dark","blurry","low_res"} that must be set
    expect_clean: bool = False  # must have NO quality flags
    note: str = ""


def _scene(w: int = 800, h: int = 600, seed: int = 1) -> Image.Image:
    rnd = random.Random(seed)
    img = Image.new("RGB", (w, h), (235, 235, 235))
    draw = ImageDraw.Draw(img)
    for _ in range(40):
        x, y = rnd.randint(0, max(1, w - 80)), rnd.randint(0, max(1, h - 80))
        draw.rectangle(
            [x, y, x + rnd.randint(20, 200), y + rnd.randint(20, 150)],
            fill=tuple(rnd.randint(20, 200) for _ in range(3)),
            outline=(0, 0, 0),
            width=2,
        )
    for i in range(12):
        draw.text((20, 20 + i * 18), "Product label text MODEL 1234 serial", fill=(0, 0, 0))
    return img


def _enc(img: Image.Image, fmt: str, **kw) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=fmt, **kw)
    return buf.getvalue()


def build_cases(settings: Settings) -> list[Case]:
    sharp = _scene()
    dark = sharp.point(lambda v: int(v * 0.1))
    blurry = sharp.filter(ImageFilter.GaussianBlur(10))
    png = _enc(sharp, "PNG")
    exif = Image.new("RGB", (100, 50), (10, 200, 10))
    exif_tags = exif.getexif()
    exif_tags[0x0112] = 6
    return [
        Case("sharp_normal.jpg", _enc(sharp, "JPEG"), "image/jpeg", None, expect_clean=True, note="control: must be unflagged"),
        Case("large_4000x3000.jpg", _enc(_scene(4000, 3000), "JPEG", quality=70), "image/jpeg", None, note="downscaled before the model"),
        Case("transparent.png", _enc(Image.new("RGBA", (400, 400), (0, 0, 0, 0)), "PNG"), "image/png", None, note="flattened onto white"),
        Case("exif_rotated.jpg", _enc(exif, "JPEG", exif=exif_tags.tobytes()), "image/jpeg", None, note="orientation applied"),
        Case("webp_ok.webp", _enc(sharp, "WEBP"), "image/webp", None),
        Case("png_claims_jpeg.jpg", png, "image/jpeg", None, note="real format wins over declared type"),
        Case("dark.jpg", _enc(dark, "JPEG"), "image/jpeg", None, expect_flags=frozenset({"dark"})),
        Case("blurry.jpg", _enc(blurry, "JPEG"), "image/jpeg", None, expect_flags=frozenset({"blurry"})),
        Case("low_res_160x120.jpg", _enc(_scene(160, 120), "JPEG"), "image/jpeg", None, expect_flags=frozenset({"low_res"})),
        Case("dimensions_5000x4000.png", _enc(Image.new("RGB", (5000, 4000), (9, 9, 9)), "PNG"), "image/png", "invalid_image"),
        Case("tiny_20x20.png", _enc(Image.new("RGB", (20, 20), (9, 9, 9)), "PNG"), "image/png", "invalid_image"),
        Case("truncated.png", png[: len(png) // 2], "image/png", "invalid_image", note="half a file"),
        Case("not_an_image.jpg", b"hello, I am a text file", "image/jpeg", "invalid_image"),
        Case("pdf_named_jpg.jpg", b"%PDF-1.4 fake", "image/jpeg", "invalid_image"),
        Case("empty.jpg", b"", "image/jpeg", "invalid_image"),
        Case("gif_unsupported.gif", _enc(Image.new("P", (100, 100), 1), "GIF"), "image/gif", "invalid_image"),
        Case("bmp_unsupported.bmp", _enc(Image.new("RGB", (100, 100), (1, 2, 3)), "BMP"), "image/bmp", "invalid_image"),
        Case("declared_pdf.jpg", _enc(sharp, "JPEG"), "application/pdf", "invalid_image", note="wrong declared MIME"),
        Case(
            "over_size_limit.jpg",
            os.urandom(settings.max_image_size_bytes + 1024),
            "image/jpeg",
            "image_too_large",
            note=f"> {settings.max_image_size_mb} MB",
        ),
    ]


@dataclass
class Outcome:
    case: Case
    accepted: bool
    error_code: str | None
    flags: set[str]
    detail: str
    passed: bool


def run_offline(settings: Settings | None = None) -> list[Outcome]:
    settings = settings or Settings(gemini_api_key="offline", _env_file=None)
    service = ImageService(settings)
    outcomes = []
    for case in build_cases(settings):
        try:
            payload = service.process(UploadedImage(case.data, case.name, case.content_type))
        except AppError as exc:
            ok = case.expect_error == exc.error_code
            outcomes.append(Outcome(case, False, exc.error_code, set(), exc.public_message, ok))
            continue
        q = payload.quality
        flags = {n for n, on in (("dark", q.is_dark), ("blurry", q.is_blurry), ("low_res", q.is_low_res)) if on}
        ok = case.expect_error is None and case.expect_flags <= flags and not (case.expect_clean and flags)
        detail = f"bright={q.mean_brightness} sharp={q.sharpness} edge={q.longest_edge}px"
        outcomes.append(Outcome(case, True, None, flags, detail, ok))
    return outcomes


def render_table(outcomes: list[Outcome]) -> str:
    rows = [f"{'CASE':28} {'RESULT':22} {'FLAGS':18} {'EXPECTED':10}  NOTE"]
    rows.append("-" * 110)
    for o in outcomes:
        result = "accepted" if o.accepted else f"rejected ({o.error_code})"
        expected = "ok" if o.passed else "MISMATCH"
        rows.append(f"{o.case.name:28} {result:22} {','.join(sorted(o.flags)) or '-':18} {expected:10}  {o.case.note}")
    return "\n".join(rows)


def render_markdown(outcomes: list[Outcome]) -> str:
    lines = [
        "# Edge-case check (offline)",
        "",
        "Generated by `python -m scripts.edge_cases --md evaluation/EDGE_CASES.md`. "
        "Covers the image pipeline only (validation, normalization, quality flags). "
        "No call is made to the vision model, so this says nothing about AI answer quality.",
        "",
        "| Case | Result | Quality flags | As designed | Note |",
        "|---|---|---|---|---|",
    ]
    for o in outcomes:
        result = "accepted" if o.accepted else f"rejected `{o.error_code}`"
        lines.append(
            f"| `{o.case.name}` | {result} | {', '.join(sorted(o.flags)) or '-'} | "
            f"{'yes' if o.passed else '**NO**'} | {o.case.note} |"
        )
    passed = sum(o.passed for o in outcomes)
    lines += ["", f"**{passed}/{len(outcomes)} cases behaved as designed.**", ""]
    return "\n".join(lines)


def report_folder(folder: Path) -> None:
    settings = Settings(gemini_api_key="offline", _env_file=None)
    service = ImageService(settings)
    print(f"{'FILE':44} {'BRIGHT':>7} {'SHARP':>9} {'EDGE':>6}  FLAGS")
    for path in sorted(p for p in folder.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".jfif", ".png", ".webp"}):
        try:
            q = service.process(UploadedImage(path.read_bytes(), path.name)).quality
            flags = ", ".join(q.problems) or "-"
            print(f"{path.name[:43]:44} {q.mean_brightness:7.1f} {q.sharpness:9.1f} {q.longest_edge:6}  {flags}")
        except AppError as exc:
            print(f"{path.name[:43]:44} rejected: {exc.error_code}")
    print(f"\nThresholds: dark < {settings.dark_threshold}, blurry < {settings.blur_threshold}, "
          f"low-res < {settings.low_res_edge}px (env: DARK_THRESHOLD, BLUR_THRESHOLD, LOW_RES_EDGE)")


async def run_with_model(outcomes: list[Outcome]) -> None:
    from app.services.listing_service import ListingService
    from app.services.vision_service import ResilientVision, VisionService

    settings = get_settings()
    service = ListingService(ImageService(settings), ResilientVision(VisionService(settings), settings), settings)
    print("\n--- accepted cases sent to the model ---")
    for o in outcomes:
        if not o.accepted:
            continue
        try:
            listing = await service.generate([UploadedImage(o.case.data, o.case.name, o.case.content_type)])
            print(f"{o.case.name:28} OK   {listing.category.value} | {listing.condition_estimate.value} | {listing.detected_item}")
        except AppError as exc:
            print(f"{o.case.name:28} FAIL {exc.error_code}: {exc.public_message}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--md", type=Path)
    parser.add_argument("--save", type=Path)
    parser.add_argument("--folder", type=Path)
    parser.add_argument("--with-model", action="store_true")
    args = parser.parse_args()

    if args.folder:
        report_folder(args.folder)
        return 0

    outcomes = run_offline()
    print(render_table(outcomes))
    ok = sum(o.passed for o in outcomes)
    print(f"\n{ok}/{len(outcomes)} behaved as designed")
    if args.md:
        args.md.write_text(render_markdown(outcomes), encoding="utf-8")
        print(f"Wrote {args.md}")
    if args.save:
        args.save.mkdir(parents=True, exist_ok=True)
        for o in outcomes:
            if o.case.name != "over_size_limit.jpg":  # skip the 10 MB junk file
                (args.save / o.case.name).write_bytes(o.case.data)
        print(f"Saved sample files to {args.save}")
    if args.with_model:
        asyncio.run(run_with_model(outcomes))
    return 0 if ok == len(outcomes) else 1


if __name__ == "__main__":
    sys.exit(main())
