"""Export the FastAPI OpenAPI schema for the mini-app (no server, no Settings/.env)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from svoi_pravila.api.app import create_app
from svoi_pravila.application.use_cases.check_readiness import CheckReadiness
from svoi_pravila.config import Environment


def export_openapi(destination: Path) -> None:
    app = create_app(CheckReadiness(probes=(), timeout_seconds=1.0), Environment.TEST)
    schema = app.openapi()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Destination path for openapi.json",
    )
    args = parser.parse_args()
    export_openapi(args.out)


if __name__ == "__main__":
    main()
