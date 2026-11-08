"""Render tests for the Streamlit page using Streamlit's AppTest harness (no browser)."""

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(Path(__file__).resolve().parents[1] / "streamlit_app" / "app.py")

LISTING = {
    "detected_item": "Samsung Tablet with Stylus",
    "category": "Mobiles and Tablets",
    "condition_estimate": "Unable to Determine",
    "suggested_title": "Samsung Tablet with Stylus - Silver",
    "suggested_description": "Silver tablet with a visible Samsung logo.",
    "extracted_features": ["Samsung logo on rear panel", "Silver back panel"],
    "tags": ["samsung", "tablet", "stylus", "silver", "touchscreen"],
}


def run(**state) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=20)
    for key, value in state.items():
        at.session_state[key] = value
    return at.run()


def texts(elements) -> str:
    return " ".join(e.value for e in elements)


def test_initial_page_renders_without_errors_and_generate_is_disabled():
    at = run()
    assert not at.exception
    assert "AI Marketplace Listing Generator" in at.title[0].value
    generate = next(b for b in at.button if b.label == "Generate Listing")
    assert generate.disabled
    assert "Upload one or more product images" in texts(at.caption)


def test_result_is_rendered_with_editable_fields():
    at = run(listing=LISTING, result_id=1, error=None)
    assert not at.exception
    title = next(t for t in at.text_input if t.label.startswith("Title"))
    assert title.value == LISTING["suggested_title"] and title.max_chars == 70
    assert at.text_area[0].value == LISTING["suggested_description"]
    markdown = texts(at.markdown)
    assert "Mobiles and Tablets" in markdown and "Samsung logo on rear panel" in markdown
    assert "`samsung`" in markdown and "`touchscreen`" in markdown
    assert len(at.json) == 1  # raw JSON panel
    assert "Condition can't be verified" in texts(at.info)


def test_known_condition_hides_the_undetermined_notice():
    listing = {**LISTING, "condition_estimate": "Used - Good Condition"}
    at = run(listing=listing, result_id=1, error=None)
    assert not at.exception and len(at.info) == 0


def test_title_edit_updates_character_counter():
    at = run(listing=LISTING, result_id=1, error=None)
    title = next(t for t in at.text_input if t.label.startswith("Title"))
    title.set_value("Short title").run()
    assert "11/70 characters" in texts(at.caption)


def test_api_error_is_shown_with_hint_and_request_id():
    error = {"message": "Image 1: not readable", "code": "invalid_image", "request_id": "abc123"}
    at = run(listing=None, result_id=0, error=error)
    assert not at.exception
    assert at.error[0].value == "Image 1: not readable"
    captions = texts(at.caption)
    assert "real JPG, PNG or WEBP" in captions and "abc123" in captions
