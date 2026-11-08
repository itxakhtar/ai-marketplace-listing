"""Streamlit UI for the AI Marketplace Listing Generator.

Run (with the API already running):  streamlit run streamlit_app/app.py
"""

import json
import os
import sys
from pathlib import Path

# Make `streamlit_app` importable no matter which directory Streamlit is launched from.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st  # noqa: E402

from streamlit_app.api_client import (  # noqa: E402
    DEFAULT_API_URL,
    ApiError,
    ApiUnavailable,
    check_health,
    generate_listing,
)

TITLE_MAX = 70
MAX_FILES = 6
MAX_FILE_MB = 10
UPLOAD_TYPES = ["jpg", "jpeg", "jfif", "png", "webp"]

st.set_page_config(page_title="AI Marketplace Listing Generator", page_icon="🛍️", layout="wide")


def init_state() -> None:
    st.session_state.setdefault("listing", None)  # last successful API response
    st.session_state.setdefault("result_id", 0)  # changes per result so edit boxes reset
    st.session_state.setdefault("error", None)


def default_api_url() -> str:
    """API URL from the environment, then Streamlit secrets, then localhost."""
    value = os.getenv("API_BASE_URL")
    if not value:
        try:
            value = str(st.secrets["API_BASE_URL"])
        except Exception:
            value = ""
    return value.strip() or DEFAULT_API_URL


def render_sidebar() -> str:
    with st.sidebar:
        st.header("Settings")
        api_url = st.text_input("API URL", value=default_api_url(), key="api_url")
        if st.button("Check API connection"):
            with st.spinner("Contacting the API (a sleeping server can take up to a minute)..."):
                reachable = check_health(api_url, timeout=60.0)
            if reachable:
                st.success("API is reachable.")
            else:
                st.error("API is not reachable. Start it with `uvicorn app.main:app`.")
        st.caption("The UI calls the FastAPI service only. No API keys are stored or shown here.")
    return api_url


def render_error(error: dict) -> None:
    st.error(error["message"])
    hints = {
        "invalid_image": "Check the file is a real JPG, PNG or WEBP image and not corrupted.",
        "invalid_request": "Upload at least one image.",
        "image_too_large": "Use a smaller image (max 10 MB each).",
        "no_product_detected": "Upload a clearer photo where one product is the main subject.",
        "ai_output_invalid": "The AI answer failed validation. Try again, or use a clearer photo.",
        "vision_rate_limited": "Too many requests. Wait about 30 seconds and try again.",
        "vision_timeout": "The AI service was slow. Try again.",
        "request_timeout": "That took too long. Try fewer or smaller images.",
        "vision_unavailable": "The AI service is temporarily unavailable. Try again shortly.",
        "vision_misconfigured": "The server's AI configuration needs attention (check the API key and model).",
    }
    if error.get("code") in hints:
        st.caption(hints[error["code"]])
    if error.get("request_id"):
        st.caption(f"Request ID: `{error['request_id']}` (include this when reporting a problem)")


def render_uploads() -> list:
    st.header("Upload Product Images")
    st.caption(
        "All images are treated as different views of the **same** product and produce one listing. "
        "JPG, PNG or WEBP · up to 6 images · 10 MB each."
    )
    files = st.file_uploader(
        "Drag & drop product images", type=UPLOAD_TYPES, accept_multiple_files=True
    )
    if files:
        columns = st.columns(min(len(files), 4))
        for index, file in enumerate(files):
            columns[index % len(columns)].image(file, caption=file.name, width="stretch")
    files = files or []
    if len(files) > MAX_FILES:
        st.error(f"Too many images: {len(files)} selected, the maximum is {MAX_FILES}.")
        return []
    too_big = [f.name for f in files if f.size > MAX_FILE_MB * 1024 * 1024]
    if too_big:
        st.error(f"Larger than {MAX_FILE_MB} MB: {', '.join(too_big)}. Please choose smaller files.")
        return []
    return files


def run_generation(api_url: str, files: list) -> None:
    payload = [(f.name, f.getvalue(), f.type) for f in files]
    with st.spinner("Analyzing images with the vision model… (usually 5–20 seconds)"):
        try:
            listing = generate_listing(api_url, payload)
        except ApiError as exc:
            st.session_state.listing = None
            st.session_state.error = {
                "message": exc.message,
                "code": exc.code,
                "request_id": exc.request_id,
            }
        except ApiUnavailable as exc:
            st.session_state.listing = None
            st.session_state.error = {"message": str(exc), "code": "unavailable", "request_id": None}
        else:
            st.session_state.listing = listing
            st.session_state.result_id += 1
            st.session_state.error = None


def render_result(listing: dict) -> None:
    rid = st.session_state.result_id
    left, right = st.columns(2, gap="large")

    with left:
        st.subheader("Generated Listing")
        st.markdown(f"**Item**  \n{listing['detected_item']}")
        c1, c2 = st.columns(2)
        c1.markdown(f"**Category**  \n{listing['category']}")
        c2.markdown(f"**Condition**  \n{listing['condition_estimate']}")
        if listing["condition_estimate"] == "Unable to Determine":
            st.info(
                "Condition can't be verified from these photos. "
                "Add clearer photos that show wear, or set it yourself when you post."
            )

        title = st.text_input(
            "Title (editable)", value=listing["suggested_title"], max_chars=TITLE_MAX, key=f"title_{rid}"
        )
        st.caption(f"{len(title)}/{TITLE_MAX} characters")
        description = st.text_area(
            "Description (editable)", value=listing["suggested_description"], height=180, key=f"desc_{rid}"
        )

        st.markdown("**Extracted features**")
        if listing.get("extracted_features"):
            st.markdown("\n".join(f"- {feature}" for feature in listing["extracted_features"]))
        else:
            st.caption("No features were extracted.")

        st.markdown("**Search tags**")
        st.markdown(" ".join(f"`{tag}`" for tag in listing["tags"]))

        edited = {**listing, "suggested_title": title, "suggested_description": description}
        st.download_button(
            "Download edited listing (JSON)",
            data=json.dumps(edited, indent=2, ensure_ascii=False),
            file_name="listing.json",
            mime="application/json",
        )

    with right:
        st.subheader("Raw JSON")
        st.caption("Exactly what the API returned (your edits are not reflected here).")
        st.json(listing)


def main() -> None:
    init_state()
    st.title("🛍️ AI Marketplace Listing Generator")
    api_url = render_sidebar()
    files = render_uploads()

    if st.button("Generate Listing", type="primary", disabled=not files):
        run_generation(api_url, files)

    st.divider()
    if st.session_state.error:
        render_error(st.session_state.error)
    elif st.session_state.listing:
        render_result(st.session_state.listing)
    else:
        st.caption("Upload one or more product images, then click **Generate Listing**.")


main()
