"""Pure presentation helpers (no Streamlit needed)."""

import io
from types import SimpleNamespace

from PIL import Image

from streamlit_app import ui


def png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (900, 600), (10, 120, 200)).save(buf, format="PNG")
    return buf.getvalue()


def test_brand_and_logo():
    assert ui.BRAND_NAME == "SnapLot"
    assert "<svg" in ui.logo_svg(40) and 'width="40"' in ui.logo_svg(40)
    header = ui.header_html()
    assert "SnapLot" in header and "<h1" in header


def test_escaping_in_all_builders():
    bad = '<b onmouseover="x">&'
    for html_out in (
        ui.chips_html([bad]),
        ui.features_html([bad]),
        ui.badge_html(bad),
        ui.gallery_html([{"label": bad, "src": 'data:x"onerror="y', "ai": True}]),
    ):
        assert "<b onmouseover" not in html_out and 'onerror="y' not in html_out


def test_condition_tones():
    assert "sl-good" in ui.condition_badge_html("Like New")
    assert "sl-muted" in ui.condition_badge_html("Unable to Determine")
    assert "sl-muted" in ui.condition_badge_html("Something unknown")


def test_stepper_states():
    out = ui.stepper_html({"listing": "ok", "images": "skipped"}, active="expand")
    assert "sl-step-ok" in out and "sl-step-active" in out and "sl-step-skipped" in out


def test_gallery_marks_ai_and_adds_skeletons():
    out = ui.gallery_html([{"label": "A", "src": "data:image/jpeg;base64,x", "ai": True}], skeletons=2)
    assert out.count("sl-skeleton") == 2 and "sl-ai-badge" in out


def test_files_signature_changes_when_the_set_changes():
    a = SimpleNamespace(name="a.jpg", size=10)
    b = SimpleNamespace(name="b.jpg", size=20)
    assert ui.files_signature([a]) == ui.files_signature([a])
    assert ui.files_signature([a]) != ui.files_signature([a, b])
    assert ui.files_signature([a]) != ui.files_signature([SimpleNamespace(name="a.jpg", size=11)])


def test_thumb_data_uri_resizes_and_survives_garbage():
    uri = ui.thumb_data_uri(png_bytes(), edge=200)
    assert uri.startswith("data:image/jpeg;base64,")
    assert ui.thumb_data_uri(b"not an image") is None


def test_generated_data_uri_only_allows_image_mimes():
    assert ui.generated_data_uri({"data_base64": "QQ==", "mime_type": "image/png"}).startswith("data:image/png")
    assert ui.generated_data_uri({"data_base64": "QQ==", "mime_type": "text/html"}).startswith("data:image/jpeg")


def test_export_text():
    out = ui.export_text(" Title ", " Body ", ["red shoes", "sale"])
    assert out == "Title\n\nBody\n\n#redshoes #sale\n"


def test_css_hides_streamlit_chrome_and_is_responsive():
    css = ui.css()
    assert "@media (max-width: 760px)" in css and "prefers-reduced-motion" in css
    assert "stSidebar" in css and "footer" in css
