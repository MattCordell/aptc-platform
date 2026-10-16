# How CI runs the backend tests

This note explains which CI job runs which tests, so you can read a red check or change the
workflow without repeating the analysis behind it. The workflow is
[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml). The change record is issue #641.

## Which job runs what

| Job | Tests | Network | Coverage |
|---|---|---|---|
| `python` | everything except container tests (`-m "not integration"`) | open | writes `.coverage.python` |
| `transform-offline` | `transform/tests`, `shared/tests` | blocked (NFR-37) | none |
| `backend-integration` | `backend/tests`, `scripts/tests` | blocked except Docker (NFR-37, NFR-39) | writes `.coverage.integration` |
| `coverage` | none | not needed | combines both files and enforces 80% |

Each test runs at least once, and every test directory in `testpaths` runs at least once under
the egress block. The `python` job and `backend-integration` overlap on the backend tests that
need no container. Those tests are cheap, so the overlap costs little.

`Required (CI)` waits on every job above, including `coverage`. A skipped job counts as a pass,
and a failed or cancelled job fails the check.

## Sharding

`backend-integration` runs `pytest -n auto --dist loadscope`. The runner has 4 vCPUs, so
`-n auto` starts 4 workers. The container fixtures (`postgres_container`, `owner_engine`,
`app_engine`) are session-scoped per worker, so each worker gets its own Postgres container.
`--dist loadscope` keeps a module on one worker. Tests in different modules therefore never
share a database, and lock-ordering and race tests cannot collide across workers.

## Coverage floor

The 80% floor applies to the combined data, not to either job alone. Neither job sees the whole
suite: `python` skips the container tests, and `backend-integration` skips `transform/tests` and
`shared/tests`. The `coverage` job downloads both data files, runs `coverage combine`, and runs
`coverage report --fail-under=80`. The setting `relative_files = true` in `pyproject.toml` stores
paths relative to the repo root, so the files combine without path remapping. If you remove it,
the combine depends on both jobs checking out to the same absolute path. The report is uploaded
as the `coverage-xml` artifact.

## When a timeout hits

Read the job's duration in the run summary first. The timeouts sit well above the times measured on
2026-10-16 (`python` 1.4 minutes, `backend-integration` 5.7, `coverage` 0.3 against 8, 10 and
5), so a cancellation usually means a test hangs or the runner shrank. Do not raise
a timeout before you find which. To reproduce the sharded run on your machine, use the command
in [`CLAUDE.md`](../../CLAUDE.md) (`uv run pytest -n auto --dist loadscope`).
