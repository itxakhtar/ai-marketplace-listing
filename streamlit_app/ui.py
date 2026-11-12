"""Presentation helpers for the Streamlit app. Pure Python: no Streamlit import, unit-testable.

Every piece of model- or user-provided text is HTML-escaped before it reaches markup.
Nothing here knows about the API address or any key.
"""

import base64
import html
import io
from pathlib import Path

from PIL import Image

BRAND_NAME = "SnapLot"
TAGLINE = "Snap a photo. Get a ready-to-post listing."
SUBTAGLINE = "Upload your product photos and let AI write the title, description and tags, plus extra visuals."

ASSETS = Path(__file__).resolve().parent / "assets"
AI_NOTICE = (
    "AI-generated illustrations based on your photo. They are not photos of the actual item, "
    "so don't present them to buyers as real photos."
)
FOOTER_NOTE = "AI can make mistakes. Check every detail against your item before you post."

CONDITION_TONE = {
    "Brand New": "good",
    "Like New": "good",
    "Used - Good Condition": "ok",
    "Used - Fair Condition": "warn",
    "Poor Condition": "bad",
    "Unable to Determine": "muted",
}

STEPS = (
    ("listing", "Reading your photos"),
    ("expand", "Writing the full description"),
    ("images", "Creating related visuals"),
)


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


# --------------------------------------------------------------------- branding
def logo_svg(size: int = 44) -> str:
    svg = (ASSETS / "logo.svg").read_text(encoding="utf-8")
    return svg.replace("<svg ", f'<svg width="{size}" height="{size}" ', 1)


def header_html() -> str:
    return (
        '<div class="sl-header">'
        f'<div class="sl-logo">{logo_svg(46)}</div>'
        f'<div><h1 class="sl-brand">{esc(BRAND_NAME)}</h1>'
        f'<div class="sl-tag">{esc(TAGLINE)}</div></div>'
        "</div>"
    )


def hero_html() -> str:
    cards = (
        ("1", "Upload", "Drop in one or more photos of the same item."),
        ("2", "Review", "Get a title, short and full description, features and tags."),
        ("3", "Post", "Edit anything, copy it, and list it anywhere."),
    )
    items = "".join(
        f'<div class="sl-how"><span class="sl-num">{n}</span><b>{esc(t)}</b><span>{esc(d)}</span></div>'
        for n, t, d in cards
    )
    return f'<div class="sl-how-row">{items}</div>'


# ------------------------------------------------------------------- components
def badge_html(text: str, tone: str = "muted") -> str:
    return f'<span class="sl-badge sl-{esc(tone)}">{esc(text)}</span>'


def condition_badge_html(condition: str) -> str:
    return badge_html(condition, CONDITION_TONE.get(condition, "muted"))


def chips_html(tags: list[str]) -> str:
    return '<div class="sl-chips">' + "".join(f'<span class="sl-chip">{esc(t)}</span>' for t in tags) + "</div>"


def features_html(features: list[str]) -> str:
    if not features:
        return '<div class="sl-muted">No features were extracted.</div>'
    return '<ul class="sl-features">' + "".join(f"<li>{esc(f)}</li>" for f in features) + "</ul>"


def stepper_html(done: dict[str, str], active: str | None) -> str:
    """done maps step id -> 'ok' | 'error' | 'skipped'; active is the step in progress."""
    parts = []
    for step_id, label in STEPS:
        state = "active" if step_id == active else done.get(step_id, "pending")
        mark = {"ok": "✓", "error": "!", "skipped": "–", "active": "", "pending": ""}[state]
        parts.append(f'<div class="sl-step sl-step-{state}"><span class="sl-dot">{mark}</span>{esc(label)}</div>')
    return '<div class="sl-stepper" role="status" aria-live="polite">' + "".join(parts) + "</div>"


def skeleton_lines_html() -> str:
    return (
        '<div aria-hidden="true">'
        '<div class="sl-line sl-skeleton w60"></div><div class="sl-line sl-skeleton"></div>'
        '<div class="sl-line sl-skeleton tall"></div><div class="sl-line sl-skeleton w80"></div></div>'
    )


def gallery_html(items: list[dict], skeletons: int = 0) -> str:
    """items: {label, src (data URI), ai (bool)}. Skeleton tiles are shown while loading."""
    tiles = []
    for item in items:
        badge = '<span class="sl-ai-badge">AI</span>' if item.get("ai") else ""
        tiles.append(
            f'<figure class="sl-tile"><img src="{esc(item["src"])}" alt="{esc(item["label"])}" loading="lazy">'
            f"{badge}<figcaption>{esc(item['label'])}</figcaption></figure>"
        )
    tiles += ['<div class="sl-tile sl-skeleton" aria-hidden="true"></div>'] * skeletons
    return '<div class="sl-gallery">' + "".join(tiles) + "</div>"


# ------------------------------------------------------------------------ data
def files_signature(files: list) -> tuple:
    """Identity of the current upload set: changes when the user adds, removes or swaps a photo."""
    return tuple((f.name, f.size) for f in files)


def thumb_data_uri(data: bytes, edge: int = 640) -> str | None:
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            rgb = img.convert("RGB")
        rgb.thumbnail((edge, edge))
        out = io.BytesIO()
        rgb.save(out, format="JPEG", quality=80)
        return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode("ascii")
    except Exception:  # noqa: BLE001 - an unreadable preview must never break the page
        return None


def generated_data_uri(image: dict) -> str:
    mime = image.get("mime_type") or "image/jpeg"
    if mime not in ("image/jpeg", "image/png", "image/webp"):
        mime = "image/jpeg"
    return f"data:{mime};base64,{image['data_base64']}"


def export_text(title: str, description: str, tags: list[str]) -> str:
    lines = [title.strip(), "", description.strip()]
    if tags:
        lines += ["", " ".join("#" + t.replace(" ", "") for t in tags)]
    return "\n".join(lines).strip() + "\n"


ERROR_HINTS = {
    "invalid_image": "Check that each file is a real JPG, PNG or WEBP photo and not corrupted.",
    "invalid_request": "Upload at least one photo.",
    "image_too_large": "Use a smaller photo (10 MB max each).",
    "request_too_large": "Use fewer or smaller photos.",
    "no_product_detected": "Upload a clearer photo where one product is the main subject.",
    "ai_output_invalid": "The AI answer didn't pass our quality checks. Try again or use a clearer photo.",
    "vision_rate_limited": "We're getting a lot of requests. Wait about 30 seconds and try again.",
    "vision_timeout": "The AI was slow. Try again.",
    "request_timeout": "That took too long. Try fewer or smaller photos.",
    "vision_unavailable": "The AI service is temporarily unavailable. Try again shortly.",
    "vision_misconfigured": "The service is being set up. Please try again later.",
    "feature_disabled": "This feature is switched off.",
}


# -------------------------------------------------------------------------- CSS
def css() -> str:
    return _CSS


_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

:root {
  --sl-bg: #F6F7FB; --sl-card: #FFFFFF; --sl-ink: #14172B; --sl-sub: #5B6178; --sl-line: #E5E8F2;
  --sl-primary: #6D5DFB; --sl-teal: #14B8A6; --sl-radius: 18px;
  --sl-grad: linear-gradient(135deg, #6D5DFB 0%, #14B8A6 100%);
  --sl-shadow: 0 1px 2px rgba(20,23,43,.04), 0 8px 24px rgba(20,23,43,.06);
}
html, body, .stApp, [class*="st-"] button, input, textarea { font-family: 'Inter', system-ui, -apple-system, 'Segoe UI', sans-serif; }
.stApp { background: radial-gradient(1200px 500px at 85% -10%, rgba(109,93,251,.10), transparent 60%),
                     radial-gradient(900px 400px at -10% 0%, rgba(20,184,166,.10), transparent 55%), var(--sl-bg); color: var(--sl-ink); }

/* hide Streamlit chrome */
header[data-testid="stHeader"], #MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"],
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"], [data-testid="collapsedControl"] { display: none !important; }
.block-container { max-width: 1120px; padding: 1.4rem 1.2rem 4rem; }

/* header */
.sl-header { display: flex; align-items: center; gap: 14px; margin: .2rem 0 1.4rem; }
.sl-logo svg { display: block; border-radius: 14px; box-shadow: 0 6px 18px rgba(109,93,251,.35); }
.sl-header h1.sl-brand { font-size: 1.55rem !important; font-weight: 800; letter-spacing: -.02em; line-height: 1.1; margin: 0 !important; padding: 0 !important; color: var(--sl-ink); }
.sl-tag { color: var(--sl-sub); font-size: .95rem; margin-top: 2px; }
.sl-section { font-size: 1.05rem; font-weight: 700; margin: 0 0 .5rem; letter-spacing: -.01em; }
.sl-muted { color: var(--sl-sub); font-size: .9rem; }

/* cards: st.container(border=True) */
[data-testid="stVerticalBlockBorderWrapper"]:has(> div > [data-testid="stVerticalBlock"] .sl-card-marker),
div[data-testid="stVerticalBlockBorderWrapper"] { border-radius: var(--sl-radius) !important; }
div[data-testid="stVerticalBlockBorderWrapper"] { background: var(--sl-card); border: 1px solid var(--sl-line) !important; box-shadow: var(--sl-shadow); padding: .35rem .35rem; }

/* uploader */
[data-testid="stFileUploaderDropzone"] { border: 2px dashed #C9CDEB !important; background: #FAFAFF; border-radius: 16px; padding: 1.4rem 1rem; transition: border-color .15s, background .15s; }
[data-testid="stFileUploaderDropzone"]:hover { border-color: var(--sl-primary) !important; background: #F4F3FF; }
[data-testid="stFileUploader"] small { color: var(--sl-sub); }

/* buttons */
button[kind="primary"], [data-testid="stBaseButton-primary"] { background: var(--sl-grad) !important; border: 0 !important; color: #fff !important; font-weight: 600; border-radius: 12px !important; padding: .55rem 1.2rem; box-shadow: 0 6px 16px rgba(109,93,251,.30); transition: transform .12s, box-shadow .12s; }
button[kind="primary"]:hover, [data-testid="stBaseButton-primary"]:hover { transform: translateY(-1px); box-shadow: 0 10px 22px rgba(109,93,251,.38); }
button[kind="secondary"], [data-testid="stBaseButton-secondary"], [data-testid="stDownloadButton"] button { border-radius: 12px !important; border: 1px solid var(--sl-line) !important; background: #fff !important; font-weight: 600; color: var(--sl-ink) !important; }
button:disabled { opacity: .5 !important; transform: none !important; box-shadow: none !important; }
button:focus-visible, input:focus-visible, textarea:focus-visible { outline: 3px solid rgba(109,93,251,.35) !important; outline-offset: 2px; }

/* inputs */
.stTextInput input, .stTextArea textarea { border-radius: 12px !important; border: 1px solid var(--sl-line) !important; background: #fff !important; }
.stTextInput input:focus, .stTextArea textarea:focus { border-color: var(--sl-primary) !important; box-shadow: 0 0 0 3px rgba(109,93,251,.15) !important; }
.stTabs [data-baseweb="tab-list"] { gap: 6px; border-bottom: 1px solid var(--sl-line); }
.stTabs [data-baseweb="tab"] { font-weight: 600; padding: .5rem .9rem; }
.stTabs [aria-selected="true"] { color: var(--sl-primary) !important; }
.stTabs [data-baseweb="tab-highlight"] { background: var(--sl-grad) !important; height: 3px; border-radius: 3px; }
[data-testid="stExpander"] { border-radius: 14px !important; border: 1px solid var(--sl-line) !important; background: rgba(255,255,255,.7); }

/* how it works */
.sl-how-row { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin: 1rem 0 .4rem; }
.sl-how { display: flex; flex-direction: column; gap: 4px; background: var(--sl-card); border: 1px solid var(--sl-line); border-radius: 16px; padding: 14px 16px; box-shadow: var(--sl-shadow); }
.sl-how span:last-child { color: var(--sl-sub); font-size: .9rem; }
.sl-num { width: 26px; height: 26px; border-radius: 50%; background: var(--sl-grad); color: #fff; font-weight: 700; font-size: .85rem; display: grid; place-items: center; margin-bottom: 4px; }

/* stepper */
.sl-stepper { display: flex; flex-wrap: wrap; gap: 8px 18px; padding: .2rem 0 .8rem; font-size: .9rem; color: var(--sl-sub); }
.sl-step { display: flex; align-items: center; gap: 8px; font-weight: 500; }
.sl-dot { width: 20px; height: 20px; border-radius: 50%; border: 2px solid #CBD0E4; display: grid; place-items: center; font-size: .7rem; font-weight: 800; color: #fff; flex: none; }
.sl-step-ok { color: var(--sl-ink); } .sl-step-ok .sl-dot { background: var(--sl-teal); border-color: var(--sl-teal); }
.sl-step-error .sl-dot { background: #E5484D; border-color: #E5484D; } .sl-step-skipped .sl-dot { background: #CBD0E4; border-color: #CBD0E4; }
.sl-step-active { color: var(--sl-ink); font-weight: 700; } .sl-step-active .sl-dot { border-color: var(--sl-primary); border-top-color: transparent; animation: sl-spin .8s linear infinite; }
@keyframes sl-spin { to { transform: rotate(360deg); } }

/* gallery */
.sl-gallery { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
.sl-tile { position: relative; margin: 0; border-radius: 14px; overflow: hidden; background: #EEF0F8; aspect-ratio: 1 / 1; }
.sl-tile img { width: 100%; height: 100%; object-fit: cover; display: block; }
.sl-tile figcaption { position: absolute; left: 8px; top: 8px; background: rgba(20,23,43,.72); color: #fff; font-size: .72rem; font-weight: 600; padding: 3px 9px; border-radius: 999px; backdrop-filter: blur(4px); }
.sl-ai-badge { position: absolute; top: 8px; right: 8px; background: var(--sl-grad); color: #fff; font-size: .68rem; font-weight: 800; padding: 3px 8px; border-radius: 999px; letter-spacing: .04em; }
.sl-skeleton { background: linear-gradient(100deg, #EEF0F8 30%, #F8F9FD 50%, #EEF0F8 70%); background-size: 220% 100%; animation: sl-shimmer 1.3s ease-in-out infinite; }
@keyframes sl-shimmer { from { background-position: 120% 0; } to { background-position: -120% 0; } }
.sl-line { height: 14px; border-radius: 8px; margin: 12px 0; }
.sl-line.w60 { width: 60%; } .sl-line.w80 { width: 80%; } .sl-line.tall { height: 120px; border-radius: 14px; }
.sl-note { color: var(--sl-sub); font-size: .8rem; margin-top: 10px; line-height: 1.4; }

/* badges, chips, features */
.sl-badge { display: inline-block; font-size: .78rem; font-weight: 700; padding: 4px 11px; border-radius: 999px; margin: 0 6px 6px 0; }
.sl-primary { background: #ECEAFF; color: #4A3DDB; } .sl-good { background: #DDF7F1; color: #0B7A6B; } .sl-ok { background: #E7F1FF; color: #1D5FC4; }
.sl-warn { background: #FFF1D6; color: #9A5B00; } .sl-bad { background: #FFE4E4; color: #B42318; } .sl-muted.sl-badge, .sl-badge.sl-muted { background: #EEF0F6; color: #4B516B; }
.sl-chips { display: flex; flex-wrap: wrap; gap: 8px; }
.sl-chip { background: #F1F0FF; color: #4A3DDB; font-size: .82rem; font-weight: 600; padding: 5px 12px; border-radius: 999px; }
.sl-features { margin: .2rem 0 0; padding-left: 1.1rem; } .sl-features li { margin: 3px 0; }
.sl-label { font-size: .8rem; font-weight: 700; text-transform: uppercase; letter-spacing: .06em; color: var(--sl-sub); margin: 14px 0 6px; }
.sl-footer { text-align: center; color: var(--sl-sub); font-size: .8rem; margin-top: 2.2rem; }

/* alerts */
[data-testid="stAlert"] { border-radius: 14px; }

/* responsive */
@media (max-width: 760px) {
  .block-container { padding: 1rem .8rem 3rem; }
  .sl-header h1.sl-brand { font-size: 1.3rem !important; }
  .sl-how-row { grid-template-columns: 1fr; }
  .sl-stepper { flex-direction: column; gap: 6px; }
  [data-testid="stHorizontalBlock"] { gap: .8rem !important; }
  [data-testid="stBaseButton-primary"], [data-testid="stBaseButton-secondary"] { width: 100%; }
}
@media (prefers-reduced-motion: reduce) { .sl-skeleton, .sl-step-active .sl-dot { animation: none; } button { transition: none !important; } }
</style>
"""
