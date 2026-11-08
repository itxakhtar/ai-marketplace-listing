import io
import json

from PIL import Image


def good_payload(**overrides) -> dict:
    data = {
        "detected_item": "Samsung Tablet",
        "category": "Mobiles and Tablets",
        "condition_estimate": "Unable to Determine",
        "suggested_title": "Samsung Tablet - Silver",
        "suggested_description": "Silver tablet with a visible Samsung logo on the rear panel.",
        "extracted_features": ["Samsung logo on rear", "Silver back panel"],
        "tags": ["samsung", "tablet", "silver", "touchscreen", "android"],
    }
    data.update(overrides)
    return data


class FakeVision:
    """Queue of canned responses (str, dict, or an Exception to raise)."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def analyze(self, images, prompt):
        self.calls.append((list(images), prompt))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, str) else json.dumps(item)


def jpeg_bytes(size=(120, 80), color=(90, 90, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()
