"""CI and the production images run the same Node, Python and uv versions.

Python and uv: every workflow installs them through the local
`.github/actions/setup-toolchain` action, which reads both tags from
`backend/Dockerfile`. A Dependabot bump of either tag therefore reaches CI with
no second edit, and nothing here compares tag literals. These tests only keep
a workflow from bypassing the action, because a direct `astral-sh/setup-uv`
step would pin its own version again.

Node: `.nvmrc` is still a hand-kept copy of the `node` tag in
`frontend/Dockerfile`, so a Dependabot PR that moves that tag fails here until
`.nvmrc` is edited; that is the signal. `.python-version` is the same kind of
copy, held to the minor version only.

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
ACTION_DIR = ROOT / ".github" / "actions" / "setup-toolchain"
ACTION_FILE = ACTION_DIR / "action.yml"
ACTION_USES = "./.github/actions/setup-toolchain"
SETUP_UV = "astral-sh/setup-uv@"
UV_COMMAND = re.compile(r"(?<![\w./-])uvx?\s")


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


def _load(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict), f"{path} is not a YAML mapping"
    return document


def _workflow_steps(workflow: Path) -> list[tuple[str, dict[str, Any]]]:
    steps: list[tuple[str, dict[str, Any]]] = []
    for job_name, job in _load(workflow).get("jobs", {}).items():
        steps.extend((f"{workflow.name}:{job_name}", step) for step in job.get("steps", []))
    return steps


def _uses(step: dict[str, Any]) -> str:
    return str(step.get("uses", ""))


def _runs_uv(workflow: Path) -> bool:
    return any(UV_COMMAND.search(str(step.get("run", ""))) for _, step in _workflow_steps(workflow))


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


def test_workflows_exist() -> None:
    assert WORKFLOWS, "no workflow files found; the checks below would pass vacuously"


def test_setup_toolchain_action_exists() -> None:
    assert ACTION_FILE.is_file(), (
        f"{ACTION_FILE.relative_to(ROOT)} is missing; every workflow's uv step depends on it"
    )


def test_some_workflow_step_uses_the_action() -> None:
    users = [
        where
        for workflow in WORKFLOWS
        for where, step in _workflow_steps(workflow)
        if _uses(step) == ACTION_USES
    ]
    assert users, (
        "no workflow step uses the setup-toolchain action; the checks would pass vacuously"
    )


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda path: path.name)
def test_no_workflow_calls_setup_uv_directly(workflow: Path) -> None:
    direct = [
        where for where, step in _workflow_steps(workflow) if _uses(step).startswith(SETUP_UV)
    ]
    assert not direct, (
        f"{', '.join(direct)} call astral-sh/setup-uv directly, which would pin its own uv and "
        f"python version; use `uses: {ACTION_USES}` so both come from backend/Dockerfile"
    )


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda path: path.name)
def test_workflow_that_runs_uv_installs_it_through_the_action(workflow: Path) -> None:
    if not _runs_uv(workflow):
        pytest.skip(f"{workflow.name} runs no uv command")
    assert any(_uses(step) == ACTION_USES for _, step in _workflow_steps(workflow)), (
        f"{workflow.name} runs uv but never uses {ACTION_USES}"
    )


def test_action_installs_uv_pinned_by_hash_from_the_dockerfile_tags() -> None:
    steps = _load(ACTION_FILE)["runs"]["steps"]
    setup_uv = [step for step in steps if _uses(step).startswith(SETUP_UV)]
    assert len(setup_uv) == 1, "the action must call astral-sh/setup-uv exactly once"
    (step,) = setup_uv
    assert re.fullmatch(r"astral-sh/setup-uv@[0-9a-f]{40}", _uses(step)), (
        f"{_uses(step)} must pin setup-uv by full commit hash"
    )
    for key in ("version", "python-version"):
        value = str(step.get("with", {}).get(key, ""))
        assert value.startswith("${{"), (
            f"setup-uv `{key}` is {value!r}; it must come from the Dockerfile tags, not a literal"
        )
