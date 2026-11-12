"""SnapLot: Streamlit UI for the AI marketplace listing generator.

Run (with the API already running):  streamlit run streamlit_app/app.py

Flow (a small state machine, one blocking API call per script run):
    upload / change photos -> "listing" (short description)
                           -> "expand"  (full description, optional)
                           -> "images"  (3 related AI visuals, optional)
Each stage renders its loading state first, then performs its call and reruns.

The API address and keys never appear on screen: the address comes from the environment or
Streamlit secrets and is only used server-side; error messages never contain it.
"""

import json
import os
import sys
from pathlib import Path

# Make `streamlit_app` importable no matter which directory Streamlit is launched from.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st  # noqa: E402
from PIL import Image  # noqa: E402

from streamlit_app import ui  # noqa: E402
from streamlit_app.api_client import (  # noqa: E402
    DEFAULT_API_URL,
    ApiError,
    ApiUnavailable,
    expand_description,
    generate_images,
    generate_listing,
)

TITLE_MAX = 70
MAX_FILES = 6
MAX_FILE_MB = 10
RELATED_COUNT = 3
UPLOAD_TYPES = ["jpg", "jpeg", "jfif", "png", "webp"]

st.set_page_config(
    page_title=f"{ui.BRAND_NAME} - AI listing generator",
    page_icon=Image.open(ui.ASSETS / "logo.png"),
    layout="wide",
    initial_sidebar_state="collapsed",
)


# --------------------------------------------------------------------------- state
def init_state() -> None:
    defaults = {
        "sig": None,  # identity of the current upload set
        "stage": None,  # None | "listing" | "expand" | "images"
        "listing": None,  # last successful listing (API response)
        "result_id": 0,  # changes per listing so edit boxes reset
        "error": None,  # fatal error for the listing stage
        "long": None,  # full description text
        "long_id": 0,
        "expand_error": None,
        "images": None,  # list of generated image dicts
        "images_error": None,
        "thumbs": [],  # previews of the uploaded photos
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def reset_results() -> None:
    s = st.session_state
    s.stage = None
    s.listing = None
    s.error = None
    s.long = None
    s.expand_error = None
    s.images = None
    s.images_error = None


def set_stage(stage: str | None) -> None:
    """Button callback: runs before the next script run, so the loading state renders first."""
    s = st.session_state
    if stage == "listing":
        reset_results()
    elif stage == "expand":
        s.expand_error = None
    elif stage == "images":
        s.images_error = None
        s.images = None
    s.stage = stage


def default_api_url() -> str:
    """Service address from the environment, then Streamlit secrets. Never displayed."""
    value = os.getenv("API_BASE_URL")
    if not value:
        try:
            value = str(st.secrets["API_BASE_URL"])
        except Exception:  # no secrets file, or key missing
            value = ""
    return value.strip() or DEFAULT_API_URL


def current_listing() -> dict:
    """The listing as the user currently sees it, including their edits."""
    s = st.session_state
    listing = s.listing or {}
    rid = s.result_id
    return {
        **listing,
        "suggested_title": s.get(f"title_{rid}", listing.get("suggested_title", "")),
        "suggested_description": s.get(f"short_{rid}", listing.get("suggested_description", "")),
    }


def current_long() -> str | None:
    s = st.session_state
    return s.get(f"long_{s.result_id}_{s.long_id}", s.long) if s.long else None


# ---------------------------------------------------------------------- input area
def render_uploader() -> list:
    with st.container(border=True):
        st.markdown('<div class="sl-section">1. Add your product photos</div>', unsafe_allow_html=True)
        st.caption(
            f"JPG, PNG or WEBP · up to {MAX_FILES} photos · {MAX_FILE_MB} MB each. "
            "Use different views of the same item."
        )
        files = st.file_uploader(
            "Product photos",
            type=UPLOAD_TYPES,
            accept_multiple_files=True,
            label_visibility="collapsed",
        )
        files = files or []
        if len(files) > MAX_FILES:
            st.error(f"Too many photos: {len(files)} selected, the maximum is {MAX_FILES}.")
            files = []
        too_big = [f.name for f in files if f.size > MAX_FILE_MB * 1024 * 1024]
        if too_big:
            st.error(f"Larger than {MAX_FILE_MB} MB: {', '.join(too_big)}. Please choose smaller files.")
            files = []

        with st.expander("Options"):
            st.toggle("Create the listing as soon as I add or change a photo", value=True, key="opt_auto")
            st.toggle("Write the full description automatically", value=True, key="opt_expand")
            st.toggle("Create 3 related AI visuals", value=True, key="opt_images")
    return files


def read_options() -> dict:
    s = st.session_state
    return {
        "auto": s.get("opt_auto", True),
        "expand": s.get("opt_expand", True),
        "images": s.get("opt_images", True),
    }


def sync_files(files: list, opts: dict) -> None:
    """Restart the pipeline whenever the set of uploaded photos changes."""
    s = st.session_state
    sig = ui.files_signature(files)
    if sig == s.sig:
        return
    s.sig = sig
    reset_results()
    s.thumbs = [
        {"label": f"Your photo {i}", "src": src, "ai": False}
        for i, f in enumerate(files, start=1)
        if (src := ui.thumb_data_uri(f.getvalue()))
    ]
    if files and opts["auto"]:
        s.stage = "listing"


# ------------------------------------------------------------------------- results
def render_error(error: dict) -> None:
    st.error(error["message"])
    hint = ui.ERROR_HINTS.get(error.get("code", ""))
    if hint:
        st.caption(hint)
    if error.get("request_id"):
        st.caption(f"Reference: `{error['request_id']}` (quote this if you contact support)")


def render_progress(opts: dict) -> None:
    s = st.session_state
    done: dict[str, str] = {}
    if s.listing:
        done["listing"] = "ok"
    elif s.error:
        done["listing"] = "error"
    if s.long:
        done["expand"] = "ok"
    elif s.expand_error:
        done["expand"] = "error"
    elif not opts["expand"]:
        done["expand"] = "skipped"
    if s.images:
        done["images"] = "ok"
    elif s.images_error:
        done["images"] = "error"
    elif not opts["images"]:
        done["images"] = "skipped"
    st.markdown(ui.stepper_html(done, s.stage), unsafe_allow_html=True)


def gallery_block() -> None:
    s = st.session_state
    items = list(s.thumbs) + [
        {"label": img.get("label", "AI visual"), "src": ui.generated_data_uri(img), "ai": True}
        for img in (s.images or [])
    ]
    skeletons = RELATED_COUNT if s.stage == "images" else 0
    st.markdown('<div class="sl-section">Photos</div>', unsafe_allow_html=True)
    st.markdown(ui.gallery_html(items, skeletons), unsafe_allow_html=True)
    if s.images:
        st.markdown(f'<div class="sl-note">{ui.esc(ui.AI_NOTICE)}</div>', unsafe_allow_html=True)
        st.button("Regenerate visuals", key="regen_images", on_click=set_stage, args=("images",))
    elif s.images_error:
        st.warning(s.images_error)
        st.button("Try the visuals again", key="retry_images", on_click=set_stage, args=("images",))


def description_tabs(rid: int) -> None:
    s = st.session_state
    listing = s.listing
    short_tab, full_tab = st.tabs(["Short description", "Full description"])

    with short_tab:
        st.text_area(
            "Short description (editable)",
            value=listing["suggested_description"],
            height=150,
            key=f"short_{rid}",
            label_visibility="collapsed",
        )
        if s.long:
            st.caption("✓ A full description is ready in the **Full description** tab.")

    with full_tab:
        if s.stage == "expand":
            st.markdown(ui.skeleton_lines_html(), unsafe_allow_html=True)
            st.caption("Writing a fuller description from your verified details…")
        elif s.long:
            st.text_area(
                "Full description (editable)",
                value=s.long,
                height=340,
                key=f"long_{rid}_{s.long_id}",
                label_visibility="collapsed",
            )
            st.button("Rewrite full description", key="rewrite_long", on_click=set_stage, args=("expand",))
        else:
            if s.expand_error:
                st.warning(s.expand_error)
            st.caption("Expand the short description into a longer, buyer-friendly one.")
            st.button(
                "✨ Write full description",
                key="write_long",
                type="primary",
                on_click=set_stage,
                args=("expand",),
            )


def listing_block() -> None:
    s = st.session_state
    listing, rid = s.listing, s.result_id

    st.markdown(
        ui.badge_html(listing["category"], "primary") + ui.condition_badge_html(listing["condition_estimate"]),
        unsafe_allow_html=True,
    )
    st.markdown(f'<div class="sl-muted">Detected: <b>{ui.esc(listing["detected_item"])}</b></div>', unsafe_allow_html=True)
    if listing["condition_estimate"] == "Unable to Determine":
        st.info(
            "Condition can't be verified from these photos. "
            "Add clearer photos that show wear, or set it yourself when you post."
        )

    title = st.text_input("Title (editable)", value=listing["suggested_title"], max_chars=TITLE_MAX, key=f"title_{rid}")
    st.caption(f"{len(title)}/{TITLE_MAX} characters")

    description_tabs(rid)

    st.markdown('<div class="sl-label">Key features</div>' + ui.features_html(listing.get("extracted_features", [])), unsafe_allow_html=True)
    st.markdown('<div class="sl-label">Search tags</div>' + ui.chips_html(listing["tags"]), unsafe_allow_html=True)

    edited = current_listing()
    long_text = current_long()
    export = {**edited, **({"full_description": long_text} if long_text else {})}
    st.markdown('<div class="sl-label">Take it with you</div>', unsafe_allow_html=True)
    st.download_button(
        "Download listing (JSON)",
        data=json.dumps(export, indent=2, ensure_ascii=False),
        file_name="listing.json",
        mime="application/json",
    )
    with st.expander("Copy-ready text"):
        body = long_text or edited["suggested_description"]
        st.code(ui.export_text(edited["suggested_title"], body, listing["tags"]), language=None, wrap_lines=True)


def render_results(opts: dict) -> None:
    s = st.session_state

    if s.stage:
        render_progress(opts)

    if s.error:
        render_error(s.error)
        return

    if s.listing is None:
        if s.stage == "listing":
            left, right = st.columns([5, 6], gap="large")
            with left, st.container(border=True):
                st.markdown(ui.gallery_html(list(s.thumbs), RELATED_COUNT if opts["images"] else 0), unsafe_allow_html=True)
            with right, st.container(border=True):
                st.markdown(ui.skeleton_lines_html(), unsafe_allow_html=True)
                st.caption("Reading your photos and writing the listing… usually 5 to 20 seconds.")
        else:
            st.markdown(ui.hero_html(), unsafe_allow_html=True)
        return

    st.markdown('<div class="sl-section">2. Your listing</div>', unsafe_allow_html=True)
    left, right = st.columns([5, 6], gap="large")
    with left, st.container(border=True):
        gallery_block()
    with right, st.container(border=True):
        listing_block()

    with st.expander("Technical view (raw AI result)"):
        st.json(s.listing)


# --------------------------------------------------------------------- pipeline run
def _fail(message: str, code: str | None = None, request_id: str | None = None) -> dict:
    return {"message": message, "code": code or "unavailable", "request_id": request_id}


def run_pending(files: list, opts: dict) -> None:
    """Perform the stage that the page above is already showing as loading, then rerun."""
    s = st.session_state
    stage = s.stage
    if not stage:
        return
    if not files:
        s.stage = None
        return

    api_url = default_api_url()
    payload = [(f.name, f.getvalue(), f.type) for f in files]

    if stage == "listing":
        try:
            s.listing = generate_listing(api_url, payload)
            s.result_id += 1
            s.error = None
            s.stage = "expand" if opts["expand"] else ("images" if opts["images"] else None)
        except ApiError as exc:
            s.listing, s.error, s.stage = None, _fail(exc.message, exc.code, exc.request_id), None
        except ApiUnavailable as exc:
            s.listing, s.error, s.stage = None, _fail(str(exc)), None

    elif stage == "expand":
        try:
            s.long = expand_description(api_url, current_listing())
            s.long_id += 1
            s.expand_error = None
        except (ApiError, ApiUnavailable) as exc:
            s.expand_error = getattr(exc, "message", None) or str(exc)
        s.stage = "images" if opts["images"] and s.images is None and not s.images_error else None

    elif stage == "images":
        try:
            s.images = generate_images(api_url, payload, (s.listing or {}).get("detected_item"))
            s.images_error = None
        except (ApiError, ApiUnavailable) as exc:
            s.images_error = "Related visuals aren't available right now. Your listing is unaffected. " + (
                getattr(exc, "message", None) or str(exc)
            )
        s.stage = None

    st.rerun()


# ------------------------------------------------------------------------------ main
def main() -> None:
    st.markdown(ui.css(), unsafe_allow_html=True)
    init_state()
    st.markdown(ui.header_html(), unsafe_allow_html=True)

    files = render_uploader()
    opts = read_options()
    sync_files(files, opts)

    s = st.session_state
    if files and not s.stage:
        label = "Try again" if s.error else ("Regenerate listing" if s.listing else "Create listing")
        st.button(
            label,
            type="primary" if not s.listing else "secondary",
            on_click=set_stage,
            args=("listing",),
            key="create",
        )

    render_results(opts)
    st.markdown(f'<div class="sl-footer">{ui.esc(ui.FOOTER_NOTE)}</div>', unsafe_allow_html=True)
    run_pending(files, opts)


main()
