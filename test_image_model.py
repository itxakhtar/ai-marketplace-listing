from google import genai
from google.genai import types
from PIL import Image
import io
import os
from dotenv import load_dotenv
from app.core.config import get_settings

load_dotenv()
s = get_settings()
print("IMAGE MODEL IN USE:", s.gemini_image_model)

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

img = Image.new("RGB", (512, 512), "white")
buf = io.BytesIO()
img.save(buf, format="JPEG")
part = types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg")

try:
    r = client.models.generate_content(
        model=s.gemini_image_model,
        contents=[part, "Create a studio product photo of white wireless earbuds with charging case"],
        config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
    )
    print("SUCCESS")
    for c in r.candidates or []:
        for p in c.content.parts or []:
            print("part:", "IMAGE" if getattr(p, "inline_data", None) else "TEXT")
except Exception as e:
    print("RAW ERROR:", type(e).__name__, e)
