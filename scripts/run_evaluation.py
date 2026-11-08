"""Run the AI evaluation: every case in evaluation/dataset.json through ListingService.

Usage (project root, API key in .env):
    python -m scripts.run_evaluation --dry-run          # check which images exist, no API calls
    python -m scripts.run_evaluation                    # run, write evaluation/REPORT.md + results.json
    python -m scripts.run_evaluation --only tablet_samsung,phone_test_download
    python -m scripts.run_evaluation --runs 3           # repeat each case (stability check)
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.core.exceptions import AppError
from app.models.image import UploadedImage
from evaluation.metrics import CaseResult, score_case, summarize

EVAL_DIR = Path(__file__).resolve().parents[1] / "evaluation"

CHECK_LABELS = {
    "json_valid": "JSON validity (no ai_output_invalid)",
    "expected_outcome": "Expected outcome (listing vs. refusal)",
    "category_correct": "Category accuracy",
    "title_max_70": "Title <= 70 characters",
    "tag_count_5_to_8": "Tag count 5-8",
    "tag_relevant": "Tag relevance (keyword present)",
    "condition_reasonable": "Condition reasonableness",
    "no_brand_hallucination": "No brand hallucination",
    "no_spec_hallucination": "No unsupported specification",
}


def load_cases(only: set[str] | None) -> list[dict]:
    cases = json.loads((EVAL_DIR / "dataset.json").read_text(encoding="utf-8"))["cases"]
    return [c for c in cases if not only or c["id"] in only]


def resolve_images(case: dict) -> tuple[list[Path], list[Path]]:
    paths = [(EVAL_DIR / rel).resolve() for rel in case["images"]]
    return [p for p in paths if p.is_file()], [p for p in paths if not p.is_file()]


def dry_run(cases: list[dict]) -> int:
    ready = 0
    for case in cases:
        present, missing = resolve_images(case)
        ok = not missing
        ready += ok
        print(f"{'READY  ' if ok else 'MISSING'} {case['id']:24} {', '.join(str(m.relative_to(EVAL_DIR.parent)) for m in missing)}")
    print(f"\n{ready}/{len(cases)} cases have their images.")
    return 0


async def run(cases: list[dict], runs: int) -> list[CaseResult]:
    from app.core.config import get_settings
    from app.core.logging import configure_logging
    from app.services.image_service import ImageService
    from app.services.listing_service import ListingService
    from app.services.vision_service import ResilientVision, VisionService

    settings = get_settings()
    configure_logging("WARNING")
    service = ListingService(ImageService(settings), ResilientVision(VisionService(settings), settings), settings)

    results: list[CaseResult] = []
    for case in cases:
        present, missing = resolve_images(case)
        if missing:
            results.append(score_case(case, {"skipped": "missing image(s): " + ", ".join(m.name for m in missing)}))
            print(f"SKIP  {case['id']}")
            continue
        uploads = [UploadedImage(p.read_bytes(), p.name) for p in present]
        for attempt in range(1, runs + 1):
            try:
                outcome = {"listing": (await service.generate(uploads)).model_dump(mode="json")}
            except AppError as exc:
                outcome = {"error_code": exc.error_code}
            scored = score_case(case, outcome)
            scored.details["run"] = attempt
            results.append(scored)
            bad = scored.failed_checks
            print(f"{'PASS' if not bad else 'FAIL'}  {case['id']} (run {attempt}) {', '.join(bad)}")
    return results


def render_report(results: list[CaseResult], model: str, runs: int) -> str:
    summary = summarize(results)
    scored = [r for r in results if r.status != "skipped"]
    skipped = [r for r in results if r.status == "skipped"]
    lines = [
        "# AI Evaluation Report",
        "",
        f"- Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"- Model: `{model}`  |  Runs per case: {runs}",
        f"- Cases scored: {len(scored)}  |  Skipped (image missing): {len(skipped)}",
        "",
        "## Summary",
        "",
        "| Check | Passed | Rate |",
        "|---|---|---|",
    ]
    for name, s in summary.items():
        lines.append(f"| {CHECK_LABELS.get(name, name)} | {s['passed']}/{s['total']} | {s['rate']:.0%} |")
    lines += [
        "",
        "Automatic checks are heuristics: category/keyword/brand/spec checks come from the manifest, "
        "so a FAIL means 'look at this case', not necessarily 'the model is wrong'. "
        "Brand and spec checks only flag common names/patterns listed in `evaluation/metrics.py`.",
        "",
        "## Per-case results",
        "",
        "| Case | Kind | Result | Category | Condition | Title | Failed checks |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in scored:
        if r.listing:
            cat, cond, title = r.listing["category"], r.listing["condition_estimate"], r.listing["suggested_title"]
        else:
            cat = cond = title = "-"
        result = f"error `{r.error_code}`" if r.status == "error" else "listing"
        lines.append(f"| {r.case_id} | {r.kind} | {result} | {cat} | {cond} | {title} | {', '.join(r.failed_checks) or '-'} |")

    flagged = [r for r in scored if r.details.get("brand_mentions") or r.details.get("spec_mentions")]
    if flagged:
        lines += ["", "## Possible hallucinations to verify against the photo", ""]
        for r in flagged:
            lines.append(f"- **{r.case_id}**: brands={r.details.get('brand_mentions') or '-'}, specs={r.details.get('spec_mentions') or '-'}")
    if skipped:
        lines += ["", "## Skipped cases (add the images, then re-run)", ""]
        lines += [f"- {r.case_id}: {r.details['reason']}" for r in skipped]
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only", help="comma-separated case ids")
    parser.add_argument("--runs", type=int, default=1)
    args = parser.parse_args()

    cases = load_cases(set(args.only.split(",")) if args.only else None)
    if args.dry_run:
        return dry_run(cases)

    results = asyncio.run(run(cases, max(1, args.runs)))
    from app.core.config import get_settings

    report = render_report(results, get_settings().gemini_model, args.runs)
    (EVAL_DIR / "REPORT.md").write_text(report, encoding="utf-8")
    (EVAL_DIR / "results.json").write_text(
        json.dumps([r.__dict__ for r in results], indent=2, default=str), encoding="utf-8"
    )
    print(f"\nWrote {EVAL_DIR / 'REPORT.md'} and results.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
