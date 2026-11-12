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
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["sig"] = ()  # "no files uploaded yet": keeps injected results from being reset
    for key, value in state.items():
        at.session_state[key] = value
    return at.run()


def texts(elements) -> str:
    return " ".join(str(e.value) for e in elements)


def everything(at: AppTest) -> str:
    """All user-visible text the page produced."""
    return " ".join(
        texts(group) for group in (at.markdown, at.caption, at.error, at.info, at.warning, at.success)
    )


def button(at: AppTest, label_part: str):
    return next((b for b in at.button if label_part in b.label), None)


def test_initial_page_shows_brand_and_how_it_works_without_errors():
    at = run()
    assert not at.exception
    page = texts(at.markdown)
    assert "SnapLot" in page and "Snap a photo" in page and "sl-how-row" in page
    assert button(at, "Create listing") is None and button(at, "Regenerate") is None


def test_no_endpoint_or_key_is_ever_rendered(monkeypatch):
    monkeypatch.setenv("API_BASE_URL", "https://secret-host.internal")
    for state in (
        {},
        {"listing": LISTING, "result_id": 1, "error": None},
        {"listing": None, "error": {"message": "We can't reach the listing service right now.", "code": "unavailable", "request_id": None}},
    ):
        at = run(**state)
        assert not at.exception
        page = everything(at)
        assert "secret-host" not in page and "127.0.0.1" not in page and "API URL" not in page
        assert not at.sidebar.text_input  # no settings panel at all


def test_result_is_rendered_with_editable_fields():
    at = run(listing=LISTING, result_id=1, error=None)
    assert not at.exception
    title = next(t for t in at.text_input if t.label.startswith("Title"))
    assert title.value == LISTING["suggested_title"] and title.max_chars == 70
    assert at.text_area[0].value == LISTING["suggested_description"]
    page = texts(at.markdown)
    assert "Mobiles and Tablets" in page and "Samsung logo on rear panel" in page
    assert ">samsung<" in page and ">touchscreen<" in page  # tag chips
    assert len(at.json) == 1  # technical view
    assert "Condition can't be verified" in texts(at.info)
    assert button(at, "Create listing") is None  # no files uploaded in this test


def test_known_condition_hides_the_undetermined_notice():
    listing = {**LISTING, "condition_estimate": "Used - Good Condition"}
    at = run(listing=listing, result_id=1, error=None)
    assert not at.exception and len(at.info) == 0 and "Used - Good Condition" in texts(at.markdown)


def test_title_edit_updates_character_counter():
    at = run(listing=LISTING, result_id=1, error=None)
    title = next(t for t in at.text_input if t.label.startswith("Title"))
    title.set_value("Short title").run()
    assert "11/70 characters" in texts(at.caption)


def test_short_description_first_then_expand_button():
    at = run(listing=LISTING, result_id=1, error=None)
    assert button(at, "Write full description") is not None
    assert len(at.text_area) == 1  # only the short description exists yet


def test_full_description_appears_in_its_own_editable_box():
    at = run(listing=LISTING, result_id=1, error=None, long="A much longer description.", long_id=1)
    assert not at.exception
    assert len(at.text_area) == 2 and at.text_area[1].value == "A much longer description."
    assert button(at, "Rewrite full description") is not None and button(at, "Write full description") is None
    assert "full description is ready" in texts(at.caption)


def test_expand_failure_is_shown_without_hiding_the_listing():
    at = run(listing=LISTING, result_id=1, error=None, expand_error="The description could not be expanded.")
    assert "could not be expanded" in texts(at.warning)
    assert button(at, "Write full description") is not None


def test_generated_images_are_labelled_as_ai_with_notice():
    img = {"label": "Studio view", "mime_type": "image/jpeg", "data_base64": "QUJD", "ai_generated": True}
    at = run(listing=LISTING, result_id=1, error=None, images=[img])
    page = texts(at.markdown)
    assert "Studio view" in page and 'class="sl-ai-badge"' in page and "not photos of the actual item" in page
    assert button(at, "Regenerate visuals") is not None


def test_visuals_failure_keeps_listing_and_offers_retry():
    at = run(listing=LISTING, result_id=1, error=None, images_error="Related visuals aren't available right now.")
    assert "aren't available" in texts(at.warning) and button(at, "Try the visuals again") is not None


def test_loading_states_render_skeletons_and_progress():
    at = run(listing=LISTING, result_id=1, error=None, stage="images")
    page = texts(at.markdown)
    assert "sl-skeleton" in page and "sl-step-active" in page


def test_model_text_is_html_escaped():
    evil = {**LISTING, "tags": ["<script>alert(1)</script>", "a", "b", "c", "d"], "extracted_features": ["<img src=x onerror=1>"]}
    at = run(listing=evil, result_id=1, error=None)
    page = texts(at.markdown)
    assert "<script>" not in page and "&lt;script&gt;" in page
    assert "<img src=x" not in page and "&lt;img" in page


def test_api_error_is_shown_with_hint_and_reference():
    error = {"message": "Image 1: not readable", "code": "invalid_image", "request_id": "abc123"}
    at = run(listing=None, result_id=0, error=error)
    assert not at.exception
    assert at.error[0].value == "Image 1: not readable"
    captions = texts(at.caption)
    assert "real JPG, PNG or WEBP" in captions and "abc123" in captions
