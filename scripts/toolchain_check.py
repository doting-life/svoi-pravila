"""Compare the local toolchain with repository pins."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAILURES: list[str] = []


def _run(args: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(args, check=False, capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        return ""
    return (result.stdout or result.stderr).strip()


def _expect(label: str, expected: str, found: str) -> None:
    if found != expected:
        FAILURES.append(f"{label}: expected {expected!r}, found {found!r}")


def _python_version() -> None:
    expected = (ROOT / "backend" / ".python-version").read_text().strip()
    found = _run(
        [
            "uv",
            "run",
            "--directory",
            str(ROOT / "backend"),
            "python",
            "-c",
            "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')",
        ]
    )
    _expect("python", expected, found)
    requires = ""
    for line in (ROOT / "backend" / "pyproject.toml").read_text().splitlines():
        if line.startswith("requires-python"):
            requires = line.split("=", 1)[1].strip().strip('"')
            break
    major_minor = ".".join(expected.split(".")[:2])
    if major_minor not in requires:
        FAILURES.append(
            f"python pin {expected!r} does not match requires-python {requires!r}"
        )


def _uv_version() -> None:
    expected = (ROOT / "scripts" / "uv-version").read_text().strip()
    output = _run(["uv", "--version"])
    match = re.search(r"(\d+\.\d+\.\d+)", output)
    found = match.group(1) if match else output
    _expect("uv", expected, found)


def _node_version() -> None:
    expected = (ROOT / "miniapp" / ".nvmrc").read_text().strip().lstrip("v")
    output = _run(["node", "--version"])
    found = output.lstrip("v")
    _expect("node", expected, found)


def _pnpm_version() -> None:
    package = json.loads((ROOT / "miniapp" / "package.json").read_text())
    manager = str(package["packageManager"])
    expected = manager.split("@", 1)[1]
    output = _run(["corepack", "pnpm", "--version"], cwd=ROOT / "miniapp")
    _expect("pnpm", expected, output)


def _docker() -> None:
    if shutil.which("docker") is None:
        FAILURES.append("docker: expected an engine on PATH, found none")
        return
    result = subprocess.run(["docker", "info"], check=False, capture_output=True, text=True)
    if result.returncode != 0:
        FAILURES.append("docker: expected a running engine, found it unavailable")


def _load_pins() -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in (ROOT / "scripts" / "image-pins.env").read_text().splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        pins[key] = value
    return pins


def _image_digests() -> None:
    pins = _load_pins()
    for key in ("GITLEAKS_IMAGE", "TRIVY_IMAGE"):
        expected = pins[key]
        found = os.environ.get(key, "")
        _expect(key, expected, found)
        if "@sha256:" not in expected:
            FAILURES.append(f"{key}: expected a digest-pinned image, found {expected!r}")


def main() -> int:
    _python_version()
    _uv_version()
    _node_version()
    _pnpm_version()
    _docker()
    _image_digests()
    if FAILURES:
        for item in FAILURES:
            print(item, file=sys.stderr)
        return 1
    print("toolchain-check: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
