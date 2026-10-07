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
TOOLS = ("uv", "node", "pnpm", "docker", "git", "curl")


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


def _required_pnpm_version() -> str:
    package = json.loads((ROOT / "miniapp" / "package.json").read_text())
    manager = str(package["packageManager"])
    return manager.split("@", 1)[1]


def _resolve(name: str) -> str | None:
    path = shutil.which(name)
    if path is None:
        FAILURES.append(f"{name} missing")
        return None
    return path


def _semver(output: str) -> str:
    match = re.search(r"(\d+\.\d+\.\d+)", output)
    return match.group(1) if match else output.strip()


def _report_and_pin(name: str, path: str, version_args: list[str], expected: str | None) -> None:
    output = _run(version_args)
    first = output.splitlines()[0] if output else ""
    print(f"{name} {path} {first}")
    if expected is not None:
        found = _semver(first.lstrip("v") if name == "node" else first)
        _expect(name, expected, found)


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
        FAILURES.append(f"python pin {expected!r} does not match requires-python {requires!r}")


def _docker_plugins(docker: str) -> None:
    compose = _run([docker, "compose", "version"])
    if not compose:
        FAILURES.append("docker compose version failed")
    buildx = _run([docker, "buildx", "version"])
    if not buildx:
        FAILURES.append("docker buildx version failed")


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


def _load_grafana_pins() -> dict[str, str]:
    pins: dict[str, str] = {}
    path = ROOT / "ops" / "grafana" / "image-pins.env"
    for line in path.read_text().splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        pins[key] = value
    return pins


def _grafana_pins() -> None:
    pins = _load_grafana_pins()
    base = pins.get("GRAFANA_BASE_IMAGE", "")
    plugin_sha = pins.get("GRAFANA_PG_PLUGIN_SHA256", "")
    plugin_url = pins.get("GRAFANA_PG_PLUGIN_URL", "")
    prom_sha = pins.get("GRAFANA_PROM_PLUGIN_SHA256", "")
    prom_url = pins.get("GRAFANA_PROM_PLUGIN_URL", "")
    if "@sha256:" not in base:
        FAILURES.append(f"GRAFANA_BASE_IMAGE: expected digest pin, found {base!r}")
    if re.fullmatch(r"[0-9a-f]{64}", plugin_sha) is None:
        FAILURES.append(
            f"GRAFANA_PG_PLUGIN_SHA256: expected 64-hex digest, found {plugin_sha!r}"
        )
    if not plugin_url.startswith("https://"):
        FAILURES.append(f"GRAFANA_PG_PLUGIN_URL: expected https URL, found {plugin_url!r}")
    if re.fullmatch(r"[0-9a-f]{64}", prom_sha) is None:
        FAILURES.append(
            f"GRAFANA_PROM_PLUGIN_SHA256: expected 64-hex digest, found {prom_sha!r}"
        )
    if not prom_url.startswith("https://"):
        FAILURES.append(f"GRAFANA_PROM_PLUGIN_URL: expected https URL, found {prom_url!r}")
    dockerfile = (ROOT / "ops" / "grafana" / "Dockerfile").read_text()
    if base.split("@", 1)[0] not in dockerfile and base not in dockerfile:
        # FROM line carries tag@digest; require digest fragment present.
        digest = base.rsplit("@", 1)[-1]
        if digest not in dockerfile:
            FAILURES.append("ops/grafana/Dockerfile: GRAFANA_BASE_IMAGE digest missing")
    if plugin_sha not in dockerfile:
        FAILURES.append("ops/grafana/Dockerfile: GRAFANA_PG_PLUGIN_SHA256 missing")
    if pins.get("GRAFANA_PG_PLUGIN_URL", "") not in dockerfile:
        FAILURES.append("ops/grafana/Dockerfile: GRAFANA_PG_PLUGIN_URL missing")
    if prom_sha not in dockerfile:
        FAILURES.append("ops/grafana/Dockerfile: GRAFANA_PROM_PLUGIN_SHA256 missing")
    if pins.get("GRAFANA_PROM_PLUGIN_URL", "") not in dockerfile:
        FAILURES.append("ops/grafana/Dockerfile: GRAFANA_PROM_PLUGIN_URL missing")


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
    resolved: dict[str, str] = {}
    for name in TOOLS:
        path = _resolve(name)
        if path is not None:
            resolved[name] = path

    pins = {
        "uv": _required_uv_version(),
        "node": (ROOT / "miniapp" / ".nvmrc").read_text().strip().lstrip("v"),
        "pnpm": _required_pnpm_version(),
    }
    version_flags = {
        "uv": ["--version"],
        "node": ["--version"],
        "pnpm": ["--version"],
        "docker": ["--version"],
        "git": ["--version"],
        "curl": ["--version"],
    }
    for name in TOOLS:
        path = resolved.get(name)
        if path is None:
            continue
        _report_and_pin(name, path, [path, *version_flags[name]], pins.get(name))
        if name == "docker":
            _docker_plugins(path)

    if "uv" in resolved:
        _python_version()
    _image_digests()
    _grafana_pins()
    _dockerfile_tags()
    if FAILURES:
        for item in FAILURES:
            print(item, file=sys.stderr)
        return 1
    print("toolchain-check: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
