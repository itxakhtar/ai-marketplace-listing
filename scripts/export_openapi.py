"""Write docs/openapi.json. No API key or network needed.

    python -m scripts.export_openapi
"""

import json
from pathlib import Path

from app.core.config import Settings
from app.main import create_app

OUT = Path(__file__).resolve().parents[1] / "docs" / "openapi.json"


def main() -> None:
    settings = Settings(gemini_api_key="not-a-real-key", _env_file=None)  # type: ignore[call-arg]
    schema = create_app(settings=settings, listing_service=object()).openapi()  # type: ignore[arg-type]
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT} ({len(schema['paths'])} paths)")


if __name__ == "__main__":
    main()
