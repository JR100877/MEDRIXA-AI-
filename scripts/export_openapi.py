"""Reproduce the HTTP contract contracts/http/openapi.v1.json (no network, no server)."""
import json
from pathlib import Path

from app.web.app import create_app
from app.web.settings import Settings

TARGET = Path(__file__).resolve().parents[1] / "contracts" / "http" / "openapi.v1.json"
# Placeholder used only to build the app offline; it signs nothing and is not a credential.
_EXPORT_ONLY_SECRET = "export-only-not-a-credential-0123456789-abcdefgh"  # noqa: S105 - offline schema build only


def build() -> dict:
    app = create_app(Settings(environment="test", auth_secret=_EXPORT_ONLY_SECRET))
    return app.openapi()


def main():
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps(build(), indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
