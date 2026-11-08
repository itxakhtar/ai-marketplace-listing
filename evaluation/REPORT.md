# AI Evaluation Report

> **Status: partial, and honest about it.** The automated runner (`python -m scripts.run_evaluation`)
> needs a live Gemini key, which the build sandbox did not have. This file therefore contains
> (A) **real results you observed** on 2026-11-07 and (B) **what is verified offline**.
> Running the runner on your machine **overwrites this file** with a full scored report.

## A. Live run observed on 2026-11-07 (model `gemini-3.6-flash`)

Four of your own product photos, single image each, via `python -m scripts.day2_run_samples`.

| Input | Category | Condition | Attempts | Latency |
|---|---|---|---|---|
| shoes (`images.jfif`) | Fashion | Unable to Determine | 1 | ~8.3 s |
| Samsung tablet (`Samsung-Galaxy-Tab-S10-Lite-Qmart-2.jpg`) | Mobiles and Tablets | Unable to Determine | 1 | ~6.8 s |
| phone (`test_download.jpg`) | Mobiles and Tablets | Unable to Determine | 1 | ~8.0 s |
| smartwatch (`test_images1.jpg`) | Electronics | Unable to Determine | 1 | ~7.6 s |

* 4/4 listings were schema-valid on the first attempt; categories were plausible.
* Condition was conservatively `Unable to Determine` for all four (studio/catalog-style photos show no wear), which is the intended behaviour.
* **Known defect seen in the Day 1 raw output** for the tablet: *"Includes white stylus pen"* and an `s pen` tag. A prompt rule and, since Day 5, a **deterministic `accessory_claim` guardrail** now reject this. **Not yet re-verified against the live model.**
* **Not verified:** whether the phone's brand was legible in the photo, so brand correctness for that case is unchecked.

## B. Verified offline (no API key needed)

| Check | Result |
|---|---|
| Unit/integration tests (vision mocked) | 166 passing |
| 19 synthetic edge cases (corrupt, truncated, 0-byte, wrong extension, huge, EXIF-rotated, transparent PNG, ...) | 19/19 behave as documented in [`EDGE_CASES.md`](EDGE_CASES.md) |
| Live server with a fake key: corrupt file | `400 invalid_image` envelope |
| Live server with a fake key: provider unreachable | `503 vision_unavailable`, no key/stack trace in body |
| Overall request deadline | enforced (`request_timeout`, tested) |

## C. Not yet measured (run it yourself)

The dataset (`dataset.json`) has 16 cases. Three synthetic ones ship ready (`blurry`, `dark`, `landscape`);
nine need your photos (see `images/README.md`). Metrics the runner scores per case:
JSON validity, expected outcome (listing vs. refusal), category, title <= 70, tag count 5-8,
tag relevance, condition reasonableness, brand hallucination, spec hallucination.

```powershell
python -m scripts.run_evaluation --dry-run     # which cases have images
python -m scripts.run_evaluation               # scores everything present, rewrites this file
```

## D. Known limitations

* Blur/dark thresholds (`BLUR_THRESHOLD`, `DARK_THRESHOLD`) were tuned on synthetic images only. Tune on real photos with `python -m scripts.edge_cases --folder sample_images`.
* Brand/spec hallucination metrics are keyword heuristics (a list of common brands and regexes), not a human review.
* Multi-product photos pick the dominant item; this is prompt-driven and unverified live.
* Guardrails are pattern-based; they reduce, not eliminate, overclaiming.
