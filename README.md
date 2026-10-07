# Octopus Energy Service

A self-hosted, authenticated REST service for UK Octopus Energy consumption, tariff rates,
SQLite history, estimated costs and import habits. Home Assistant OS installation uses
an app/add-on plus a HACS custom integration with UI setup and native sensors.
Standalone Python/Docker and the original REST YAML package remain available.
This is an independent project, not an official Octopus product.

## Home Assistant OS quick start

1. Add this public repository to the **Apps/Add-on Store repositories**:
   `https://github.com/sebbyyys/octopus-energy-service`.
2. Install **Octopus Energy Service**, configure its protected service token and your
   Octopus API key/account number, then start it. The add-on keeps history in `/data`;
   no separate Docker server or manual YAML sensor package is required.
3. In HACS, add the same repository as a custom repository of category **Integration**
   and install **Octopus Energy Service**. Restart Home Assistant Core when prompted.
4. In **Settings → Devices & services → Add integration**, select **Octopus Energy
   Service**, enter the add-on's internal API base URL and matching service token.
   The native sensor entities are created automatically.

Follow the [HA OS app guide](docs/haos-addon.md) and
[HACS integration guide](docs/hacs-integration.md) for exact options, networking,
backup, configuration and troubleshooting. This integration uses the separate domain
`octopus_energy_service`; it does not replace existing `octopus_energy` integrations.
Choose the native integration **or** the legacy REST YAML package for a given service
unless you intentionally want duplicate sensor sets.

- Electricity import/export and gas, discovered through your account's meters and agreements.
- Idempotent history refresh: supplier corrections replace earlier interval values.
- Local-time daily/monthly analytics, including DST, standing charges and export credit.
- Authenticated API, CSV export and one shared Home Assistant polling request.
- Offline/cache-only operation when both Octopus credentials are omitted.

**Costs and gas conversions are estimates, not invoices.** Readings are delayed,
not live power. Unknown units/rates and unsupported tariffs produce nulls and
quality warnings, never invented zero costs. Intelligent smart-dispatch billing
adjustments and Economy 7/two-register tariffs are unsupported. No account balance,
payments, invoices, MQTT, device control or automatic Home Assistant history backfill.

## Local setup

Use Python 3.12+ (Docker uses 3.13) and [uv](https://docs.astral.sh/uv/).
Run these commands from the repository root:

```sh
cp .env.example .env
chmod 600 .env
# Writes a fresh service token to the private file without printing it.
python3 -c "from pathlib import Path; import secrets; p=Path('.env'); p.write_text(p.read_text().replace('OCTOPUS_SERVICE_TOKEN=', 'OCTOPUS_SERVICE_TOKEN=' + secrets.token_urlsafe(32), 1))"
uv sync --locked --group dev
```

Edit `.env` locally: set **both** `OCTOPUS_API_KEY` and `OCTOPUS_ACCOUNT_NUMBER`
to enable account synchronization, or leave both blank for offline mode. The
Octopus API key and locally generated service token are different credentials.
The blank example token deliberately prevents accidental startup with a shared secret.
Generate once after copying; do not rerun the replacement on an already populated file.

```sh
uv run --locked uvicorn octopus_service.api:create_app --factory --host 127.0.0.1 --port 8080 --workers 1
```

In another terminal, `curl --fail http://127.0.0.1:8080/health/live` checks liveness.
Use the [authenticated API examples](docs/api.md) to inspect status and meters.
Configure each gas meter's unit explicitly before trusting gas energy/costs.

## Docker Compose

With Docker Engine and the Compose v2 plugin, prepare `.env` as above, then:

```sh
docker compose up --build -d
docker compose ps
curl --fail http://127.0.0.1:8080/health/live
```

The locked image runs as UID/GID 10001, one worker, with a persistent named SQLite
volume and port **127.0.0.1:8080** by default. Other devices/containers cannot use
that loopback address; see [deployment and LAN access](docs/deployment.md) before
changing it. Never expose this service directly to the public internet.

## Home Assistant

Copy [the package](examples/home-assistant/octopus.yaml), merge
[the placeholder secrets](examples/home-assistant/secrets.example.yaml), and follow
[Home Assistant setup](docs/home-assistant.md). A [native dashboard example](examples/home-assistant/dashboard.yaml)
is included. Cumulative energy has `state_class: total`, not `total_increasing`:
corrections can lower totals. Rolling cost and energy period sensors deliberately
do not pretend to be monotonic counters.

## Documentation and verification

- [Configuration reference](docs/configuration.md)
- [API, authentication and exports](docs/api.md)
- [Cost methods, quality and limitations](docs/analytics.md)
- [Deployment, backups and restore](docs/deployment.md)
- [Security](docs/security.md)
- [Local development and CI](docs/development.md)
- [Implementation contract](INTERFACES.md)

```sh
uv run --locked pytest
uv run --locked ruff check .
uv build --no-sources
```

Tests are offline, with synthetic HTTP fixtures and temporary SQLite databases.
Passing example tests does not imply live supplier-account verification, live HA
configuration/rendering or a completed Docker build on your host.

[MIT license](LICENSE). This repository is public and includes CI, a Home Assistant OS
app repository and a HACS custom integration. Publishing source does not deploy a service,
configure customer credentials or certify live supplier-account data. Never commit `.env`,
Home Assistant config entries, databases, exports or private installation logs.
