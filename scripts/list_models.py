"""List Gemini models your API key can use for generateContent.

Usage: python -m scripts.list_models
Use the printed name WITHOUT the "models/" prefix:
  * GEMINI_MODEL        for listings and descriptions
  * GEMINI_IMAGE_MODEL  for the related AI pictures (pick one marked "image" below)
"""

from google import genai

from app.core.config import get_settings

client = genai.Client(api_key=get_settings().gemini_api_key.get_secret_value())
for model in client.models.list():
    if "generateContent" in (model.supported_actions or []):
        name = model.name.removeprefix("models/")
        print(f"{name}{'   <- image-capable?' if 'image' in name.lower() else ''}")
