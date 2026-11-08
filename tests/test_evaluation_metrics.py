import json
from pathlib import Path

from evaluation.metrics import (
    brand_hallucinations,
    score_case,
    spec_hallucinations,
    summarize,
    tag_relevance,
)
from scripts.run_evaluation import CHECK_LABELS, load_cases, render_report
from tests.helpers import good_payload

CASE = {
    "id": "tablet",
    "kind": "product",
    "expected_categories": ["Mobiles and Tablets"],
    "required_any": ["tablet"],
    "allowed_brands": ["samsung"],
}


def listing(**o):
    return good_payload(**o)


def test_brand_allowed_when_whitelisted_and_flagged_otherwise():
    item = listing(suggested_title="Samsung Galaxy Tablet")
    assert brand_hallucinations(item, ["samsung", "galaxy"]) == []
    assert brand_hallucinations(item, []) == ["galaxy", "samsung"]


def test_brand_match_is_whole_word():
    # "pixelated" must not trigger the "pixel" brand; the fixture's own "Samsung" is allowed.
    assert brand_hallucinations(listing(suggested_description="A pixelated display"), ["samsung"]) == []
    assert "pixel" in brand_hallucinations(listing(suggested_description="A Pixel display"), ["samsung"])


def test_spec_hallucination_detected():
    hits = spec_hallucinations(listing(suggested_title="Tablet 128GB 8000mAh 120Hz"), [])
    assert any("storage_or_ram" in h for h in hits)
    assert any("battery" in h for h in hits)
    assert any("refresh_rate" in h for h in hits)


def test_spec_allowed_when_visible_in_photo():
    assert spec_hallucinations(listing(suggested_title="Tablet 128GB"), ["128gb"]) == []


def test_clean_listing_has_no_spec_hits():
    assert spec_hallucinations(listing(), []) == []


def test_tag_relevance():
    assert tag_relevance(listing(), ["tablet"]) is True
    assert tag_relevance(listing(), ["shoe"]) is False
    assert tag_relevance(listing(), []) is None


def test_good_listing_passes_everything():
    r = score_case(CASE, {"listing": listing()})
    assert r.failed_checks == [] and r.checks["category_correct"] is True


def test_wrong_category_and_hallucinated_brand_fail():
    r = score_case(CASE, {"listing": listing(category="Fashion", suggested_title="Apple Tablet")})
    assert {"category_correct", "no_brand_hallucination"} <= set(r.failed_checks)


def test_long_title_and_tag_count_fail():
    bad = listing(suggested_title="x" * 71, tags=["a", "b", "c"])
    r = score_case(CASE, {"listing": bad})
    assert {"title_max_70", "tag_count_5_to_8"} <= set(r.failed_checks)


def test_condition_reasonableness():
    case = {**CASE, "acceptable_conditions": ["Unable to Determine"]}
    assert score_case(case, {"listing": listing(condition_estimate="Like New")}).checks["condition_reasonable"] is False
    assert score_case(case, {"listing": listing()}).checks["condition_reasonable"] is True


def test_expected_refusal_passes_and_unexpected_listing_fails():
    case = {"id": "landscape", "kind": "difficult", "expect_error": "no_product_detected"}
    assert score_case(case, {"error_code": "no_product_detected"}).failed_checks == []
    assert "expected_outcome" in score_case(case, {"listing": listing()}).failed_checks


def test_unexpected_error_fails_and_ai_output_invalid_fails_json_check():
    r = score_case(CASE, {"error_code": "ai_output_invalid"})
    assert {"json_valid", "expected_outcome"} <= set(r.failed_checks)


def test_skipped_cases_do_not_count():
    skipped = score_case(CASE, {"skipped": "missing"})
    ok = score_case(CASE, {"listing": listing()})
    summary = summarize([skipped, ok])
    assert summary["title_max_70"] == {"passed": 1, "total": 1, "rate": 1.0}


def test_summary_excludes_unscored_checks():
    summary = summarize([score_case({"id": "x", "kind": "product"}, {"listing": listing()})])
    assert "category_correct" in summary and summary["category_correct"]["total"] == 0


def test_dataset_manifest_is_valid_and_covers_required_cases():
    cases = load_cases(None)
    ids = {c["id"] for c in cases}
    assert {"iphone", "laptop", "furniture", "vehicle", "tool", "blurry", "multiple_products",
            "cluttered_background", "partial_product", "unknown_brand", "dark_image",
            "non_product_landscape"} <= ids
    assert len(ids) == len(cases)
    for c in cases:
        assert c["images"], c["id"]
        assert c.get("expect_error") or "expected_categories" in c


def test_report_renders_with_table_and_flags():
    results = [
        score_case(CASE, {"listing": listing(suggested_title="Apple Tablet 128GB")}),
        score_case({"id": "gone", "kind": "product"}, {"skipped": "missing image(s): gone.jpg"}),
    ]
    text = render_report(results, "gemini-test", 1)
    assert "# AI Evaluation Report" in text and "gemini-test" in text
    assert "Possible hallucinations" in text and "Skipped cases" in text
    assert all(label for label in CHECK_LABELS.values())
