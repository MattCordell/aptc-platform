"""CI and the production images run the same Node, Python and uv versions.

Dependabot bumps the tags in `backend/Dockerfile` and `frontend/Dockerfile` but
cannot see `.nvmrc` or the `version:` and `python-version:` inputs of
`astral-sh/setup-uv`, so each Docker bump would otherwise leave CI testing an
older toolchain than the one that ships. A Dependabot PR that moves one of these
tags fails here until the paired file is edited by hand; that is the signal.

Postgres and Keycloak need no check: the integration tests read their image
tags from `deploy/compose.yml`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
SETUP_UV = "astral-sh/setup-uv@"


def _dockerfile_tags(dockerfile: str, image_pattern: str) -> list[str]:
    text = (ROOT / dockerfile).read_text(encoding="utf-8")
    return re.findall(rf"^FROM {image_pattern}[ \t]", text, flags=re.MULTILINE)


def _single_tag(dockerfile: str, image_pattern: str) -> str:
    found = _dockerfile_tags(dockerfile, image_pattern)
    assert found, (
        f"no FROM line in {dockerfile} matches {image_pattern!r}; if the image reference "
        "changed shape (a digest or a variant suffix), update the pattern in this test"
    )
    tags = set(found)
    assert len(tags) == 1, f"{dockerfile} must use exactly one tag, found {sorted(tags)}"
    return tags.pop()


def _setup_uv_steps() -> list[tuple[str, dict[str, Any]]]:
    steps: list[tuple[str, dict[str, Any]]] = []
    for workflow in WORKFLOWS:
        document = yaml.safe_load(workflow.read_text(encoding="utf-8"))
        for job_name, job in document.get("jobs", {}).items():
            for step in job.get("steps", []):
                if str(step.get("uses", "")).startswith(SETUP_UV):
                    steps.append((f"{workflow.name}:{job_name}", step))
    return steps


def test_node_version_file_matches_frontend_image() -> None:
    node_tag = _single_tag("frontend/Dockerfile", r"node:(\d+\.\d+\.\d+)-slim")
    pinned = (ROOT / ".nvmrc").read_text(encoding="utf-8").strip()
    assert pinned == node_tag, (
        f".nvmrc is {pinned} but frontend/Dockerfile ships node {node_tag}; "
        "edit .nvmrc so CI tests the Node version that ships"
    )


def test_python_version_file_is_the_minor_of_the_backend_image() -> None:
    python_tag = _single_tag("backend/Dockerfile", r"python:(\d+\.\d+\.\d+)-slim")
    pinned = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    assert python_tag.startswith(f"{pinned}."), (
        f".python-version is {pinned} but backend/Dockerfile ships python {python_tag}; "
        "keep .python-version to the same minor version"
    )


def test_setup_uv_steps_exist() -> None:
    assert _setup_uv_steps(), (
        "no setup-uv steps found; the alignment checks below would pass vacuously"
    )


@pytest.mark.parametrize(
    ("where", "step"), _setup_uv_steps(), ids=lambda value: value if isinstance(value, str) else ""
)
def test_setup_uv_version_matches_backend_image(where: str, step: dict[str, Any]) -> None:
    uv_tag = _single_tag("backend/Dockerfile", r"ghcr\.io/astral-sh/uv:(\d+\.\d+\.\d+)")
    pinned = str(step.get("with", {}).get("version", ""))
    assert pinned == uv_tag, (
        f"{where} sets uv version {pinned or '(unset, so latest)'} but backend/Dockerfile ships uv {uv_tag}; "
        "set `version:` on the setup-uv step to the Dockerfile's uv tag"
    )


@pytest.mark.parametrize(
    ("where", "step"), _setup_uv_steps(), ids=lambda value: value if isinstance(value, str) else ""
)
def test_setup_uv_python_matches_backend_image(where: str, step: dict[str, Any]) -> None:
    python_tag = _single_tag("backend/Dockerfile", r"python:(\d+\.\d+\.\d+)-slim")
    pinned = str(step.get("with", {}).get("python-version", ""))
    assert pinned == python_tag, (
        f"{where} sets python {pinned or '(unset, so .python-version floats)'} but backend/Dockerfile ships python {python_tag}; "
        "set `python-version:` on the setup-uv step to the Dockerfile's python tag"
    )
