"""Compare the local toolchain with repository pins."""

from __future__ import annotations

import json
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


def _required_uv_version() -> str:
    text = (ROOT / "backend" / "pyproject.toml").read_text()
    match = re.search(r'required-version\s*=\s*"==([^"]+)"', text)
    if match is None:
        FAILURES.append("uv: [tool.uv] required-version missing from pyproject.toml")
        return ""
    return match.group(1)


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
    expected = _required_uv_version()
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
    if not pins:
        FAILURES.append("image-pins.env: expected at least one image pin")
        return
    for key, expected in pins.items():
        if "@sha256:" not in expected:
            FAILURES.append(f"{key}: expected a digest-pinned image, found {expected!r}")


def _dockerfile_tags() -> None:
    python_expected = (ROOT / "backend" / ".python-version").read_text().strip()
    uv_expected = _required_uv_version()
    node_expected = (ROOT / "miniapp" / ".nvmrc").read_text().strip().lstrip("v")
    backend = (ROOT / "backend" / "Dockerfile").read_text()
    miniapp = (ROOT / "miniapp" / "Dockerfile").read_text()
    python_match = re.search(r"FROM python:([0-9]+\.[0-9]+\.[0-9]+)", backend)
    uv_match = re.search(r"ghcr\.io/astral-sh/uv:([0-9]+\.[0-9]+\.[0-9]+)", backend)
    node_match = re.search(r"FROM node:([0-9]+\.[0-9]+\.[0-9]+)", miniapp)
    _expect(
        "backend/Dockerfile python",
        python_expected,
        python_match.group(1) if python_match else "",
    )
    _expect(
        "backend/Dockerfile uv",
        uv_expected,
        uv_match.group(1) if uv_match else "",
    )
    _expect(
        "miniapp/Dockerfile node",
        node_expected,
        node_match.group(1) if node_match else "",
    )


def main() -> int:
    _python_version()
    _uv_version()
    _node_version()
    _pnpm_version()
    _docker()
    _image_digests()
    _dockerfile_tags()
    if FAILURES:
        for item in FAILURES:
            print(item, file=sys.stderr)
        return 1
    print("toolchain-check: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
