# Local development and verification

[Back to README](../README.md)

Use Python 3.12+ and uv from the repository root:

```sh
uv sync --locked --group dev
uv run --locked pytest
uv run --locked pytest tests/test_examples.py -q
uv run --locked ruff check .
uv build --no-sources
```

Tests use deterministic synthetic data, mock upstream transports and temporary SQLite
files, not a real account. The example suite loads YAML with a safe custom `!secret`
tag constructor, renders templates against populated/empty/stale/partial/negative-price
fixtures, checks dashboard references, and guards locked/private/persistent deployment.
These are offline fixture checks, not successful real HA configuration or visual tests.

Follow test-first RED → GREEN → refactor for behavior changes. Keep credentials out
of tests and avoid starting the scheduler against a real account during development.
Only one uvicorn worker should run; do not use multiple processes for a shared scheduler.
The source contract is [INTERFACES.md](../INTERFACES.md).

`uv.lock` is committed input to Docker and CI, not regenerated on image startup.
If changing project dependencies intentionally, run `uv lock`, inspect the change,
and retest before distributing the lockfile. An outdated lock fails `--locked` rather
than silently resolving different packages. Build commands do not publish anything.

## Local CI file

[ci.yml](../.github/workflows/ci.yml) declares read-only GitHub permissions, tests on
Python 3.12/3.13, locked uv installation, Ruff, coverage, packaging, Compose parsing
and a Docker image build. There are no publish/deploy/upload or account-secret steps.
Writing this file does not create a repository or enable integrations. Its actions
will execute only if a future approved repository/workflow is activated.

## Container acceptance

With a working Docker daemon and Compose v2 installed:

```sh
docker compose --env-file .env.example config --quiet
docker build --tag octopus-energy-service:local .
docker compose up -d
curl --fail http://127.0.0.1:8080/health/live
docker compose exec octopus id
docker compose exec octopus python -c "from pathlib import Path; p=Path('/app/data'); print(p.is_dir(), p.stat().st_uid)"
```

Prepare a private `.env` with generated token first; the example intentionally has no
working token. `docker compose config` without `--quiet` can render credentials, so
do not share its output. Verify UID/GID 10001, successful healthcheck and persistent
DB after restart. `docker compose down` keeps history; **never use `down -v`** unless
intentional deletion is acceptable. If daemon/build tools/network are unavailable,
report the blocker rather than claiming static checks exercised the image.
