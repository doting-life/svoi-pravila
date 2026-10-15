"""IMAGE_TAG: per-run local image tags, default `ci`, cleanup of exactly the run's tags."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
IMAGE_NAMES = [
    "svoi-pravila",
    "svoi-pravila-miniapp",
    "svoi-pravila-grafana",
    "svoi-pravila-backup",
]
TAG_SCRIPTS = ["image.sh", "image_scan.sh", "prod_smoke.sh", "publish.sh"]
DEFAULT_LINE = 'IMAGE_TAG="${IMAGE_TAG:-ci}"'
DOCKER_STUB = """#!/bin/bash
printf '%s\\n' "$*" >> "$STUB_LOG"
case "$*" in
  *RepoDigests*) printf '%s\\n' "${!#%:*}@sha256:abc" ;;
esac
"""


def _run_with_docker_stub(
    script: str, tmp_path: Path, extra_env: dict[str, str], *, tag: str | None
) -> list[str]:
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub = stub_dir / "docker"
    stub.write_text(DOCKER_STUB)
    stub.chmod(0o700)
    log = tmp_path / "docker.log"
    env = {key: value for key, value in os.environ.items() if key != "IMAGE_TAG"}
    env.update(extra_env)
    env["PATH"] = f"{stub_dir}:{env['PATH']}"
    env["STUB_LOG"] = str(log)
    env["SCRIPT_PATH"] = str(SCRIPTS / script)
    if tag is not None:
        env["IMAGE_TAG"] = tag
    subprocess.run(
        ["/bin/bash", "-c", '/bin/bash "$SCRIPT_PATH"'],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return log.read_text().splitlines()


PUBLISH_ENV = {
    "GHCR_TOKEN": "t",
    "GHCR_USER": "u",
    "GHCR_OWNER": "Own",
    "GITHUB_SHA": "a" * 40,
}


@pytest.mark.unit
@pytest.mark.parametrize("tag", [None, "ci-abc123"])
def test_image_script_builds_the_four_images_with_the_tag(tmp_path: Path, tag: str | None) -> None:
    calls = _run_with_docker_stub("image.sh", tmp_path, {}, tag=tag)

    expected = tag or "ci"
    built = [re.search(r"-t (\S+)", call) for call in calls]
    assert [m.group(1) for m in built if m] == [f"{name}:{expected}" for name in IMAGE_NAMES]


@pytest.mark.unit
@pytest.mark.parametrize("tag", [None, "ci-abc123"])
def test_publish_reads_local_images_by_tag_and_keeps_published_names(
    tmp_path: Path, tag: str | None
) -> None:
    calls = _run_with_docker_stub("publish.sh", tmp_path, PUBLISH_ENV, tag=tag)

    expected = tag or "ci"
    sha = "a" * 40
    tagged = [call for call in calls if call.startswith("tag ")]
    assert tagged == [
        f"tag {local}:{expected} ghcr.io/own/{published}:{sha}"
        for local, published in [
            ("svoi-pravila", "svoi-pravila-api"),
            ("svoi-pravila-miniapp", "svoi-pravila-miniapp"),
            ("svoi-pravila-grafana", "svoi-pravila-grafana"),
            ("svoi-pravila-backup", "svoi-pravila-backup"),
        ]
    ]
    inspected = [call for call in calls if call.startswith("image inspect svoi-pravila")]
    assert inspected == [f"image inspect {name}:{expected}" for name in IMAGE_NAMES]


@pytest.mark.unit
@pytest.mark.parametrize("script", TAG_SCRIPTS)
def test_every_script_defaults_to_ci_and_has_no_fixed_tag(script: str) -> None:
    text = (SCRIPTS / script).read_text()

    assert DEFAULT_LINE in text
    assert re.search(r"svoi-pravila[a-z-]*:ci\b", text.replace("*:${IMAGE_TAG}", "")) is None


@pytest.mark.unit
def test_ci_sh_sets_and_exports_the_run_tag_and_removes_exactly_its_images() -> None:
    text = (SCRIPTS / "ci.sh").read_text()
    image_script = (SCRIPTS / "image.sh").read_text()

    assert 'IMAGE_TAG="$(ci_image_tag "$CI_TMPDIR")"\nexport IMAGE_TAG' in text
    listed = re.search(r"^LOCAL_IMAGES=\((.*)\)$", text, flags=re.MULTILINE)
    assert listed is not None
    assert listed.group(1).split() == IMAGE_NAMES
    built = re.findall(r'-t "([a-z-]+):\$\{IMAGE_TAG\}"', image_script)
    assert built == IMAGE_NAMES
    assert 'docker image rm "${name}:${IMAGE_TAG}" >/dev/null 2>&1 || true' in text
    assert "prune" not in text
