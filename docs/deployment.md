# Deployment, networking and backups

[Back to README](../README.md)

## Docker

Requires Docker Engine with a running daemon and the Compose v2 plugin. Prepare the
private `.env` and fresh token using the README before starting:

```sh
docker compose --env-file .env.example config --quiet
docker compose up --build -d
docker compose ps
curl --fail http://127.0.0.1:8080/health/live
```

The runtime stage is `python:3.13-slim`; the builder uses pinned uv 0.11.6 and
`uv sync --locked --no-dev --no-editable`. Missing/outdated `uv.lock` fails the build.
Only the installed environment goes into runtime, never local secrets or SQLite files.
The base Python tag can move over time; for immutable production supply-chain pinning,
review and pin image digests yourself rather than treating this tag as a digest lock.

The image runs UID/GID **10001:10001**, one uvicorn worker with `--factory`, on container
port 8080. Compose mounts the named volume `octopus-data` at `/app/data` and overrides
`OCTOPUS_DB_PATH=/app/data/octopus.sqlite3`. New named volumes inherit the image's
owned directory. Root filesystem is read-only; only data and a small tmpfs are writable.
Capabilities are dropped and `no-new-privileges` is enabled. The liveness healthcheck
does not prove synchronization success or complete data.

Do not scale replicas or workers: each process would run a separate in-process sync
scheduler. Do not mount a host data directory without preparing its permissions.
The default named volume needs no host-directory chown. If replacing it with a bind
mount on Linux, create an owner-only directory owned by UID/GID 10001 before startup;
rootless/user-namespace Docker requires corresponding mapped ownership. Never fix this
with world-writable permissions or running the application as root.

## Home Assistant networking

Published host port defaults to **127.0.0.1:8080**. `0.0.0.0` inside the container does
not make the host publication public. `OCTOPUS_BIND_ADDRESS` changes only Compose's
host binding; local uvicorn uses its explicit `--host` command argument.

For HA on a different host, choose an actual LAN address assigned to the service host:

```dotenv
# Illustrative private address; replace with your host's real interface address.
OCTOPUS_BIND_ADDRESS=192.168.1.10
```

Recreate with `docker compose up -d --force-recreate`. HA must use a reachable URL,
not its own loopback. Restrict the port by firewall to the HA/client addresses and
prefer TLS on a reverse proxy or an encrypted VPN/tunnel. Bearer tokens on plain LAN
HTTP are visible to an on-path observer. Do not bind all interfaces as an unexplained
shortcut, open router forwarding or disable HA certificate verification.

For containerized HA on the same Docker host, use an intentionally configured shared
private Docker network and service URL `http://octopus:8080/v1/home-assistant`; the
containers must actually share that network and name. Do not assume HA OS/Supervisor
uses the Compose network. A reverse proxy may instead expose a trusted HTTPS URL
and keep the host's application port loopback-bound. Preserve Authorization headers,
limit request sizes/rates, and do not log credentials or private response bodies.

## Updates and logs

```sh
docker compose logs --tail 100 octopus
docker compose up --build -d
```

Keep logs private. Back up first and retain the previous image/config for rollback.
`docker compose down` keeps the named volume; **`docker compose down -v` deletes it**.
Never run the latter as a routine update or troubleshooting step. Deleting/recreating
history also changes Home Assistant cumulative baselines.

## Backup (stopped consistent snapshot)

The actual named volume is normally project-prefixed; discover it from the service
container instead of guessing `octopus-data`. The commands below are for the supplied
Compose configuration and default DB filename. Keep the same Compose project name:

```sh
mkdir -p backups
chmod 700 backups
VOLUME=$(docker inspect "$(docker compose ps -aq octopus)" --format '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Name}}{{end}}{{end}}')
test -n "$VOLUME" || exit 1
docker compose stop octopus
docker run --rm --user 0:0 -v "$VOLUME:/data:ro" -v "$PWD/backups:/backup" python:3.13-slim tar -C /data -czf /backup/octopus-data.tgz .
docker compose start octopus
```

Stop all writers and check the backup command succeeded before restarting. This
archives the whole volume, including any WAL/SHM sidecars, without a race. Do **not**
copy just a live `.sqlite3` file while WAL writes are occurring. For online snapshots,
use SQLite's backup API instead. Preserve `.env` separately in encrypted/restricted
storage; it is not in the volume snapshot. Protect backups at rest and test restore on
a disposable separate volume before relying on them. The helper runs as root only for
archive maintenance; the application remains non-root.

## Restore

Restoring overwrites the target data. Verify the backup belongs to this installation,
keep an independent copy of current data first, and ensure no application is writing.
Only restore trusted archives made by the backup procedure. Use the actual target
volume name discovered above (for a fresh install, create the service container first
with `docker compose create` so its named volume exists):

```sh
test -n "$VOLUME" || exit 1
docker compose stop octopus
docker run --rm --user 0:0 -v "$VOLUME:/data" -v "$PWD/backups:/backup:ro" python:3.13-slim sh -c 'test -r /backup/octopus-data.tgz && tar -tzf /backup/octopus-data.tgz >/dev/null && rm -f /data/octopus.sqlite3 /data/octopus.sqlite3-wal /data/octopus.sqlite3-shm && tar -C /data -xzf /backup/octopus-data.tgz && chown -R 10001:10001 /data'
docker compose start octopus
curl --fail http://127.0.0.1:8080/health/live
```

Confirm restore succeeded **before** starting; remove no data if the archive is absent
or unverified. Verify authenticated status, meter/history counts and HA statistics,
not just liveness. The explicit sidecar removal avoids mixing a restored database
with WAL from the previous database. Keep schema-compatible code/config with backups.

## Failure checks

- Lock/build failure: inspect lock/project consistency; do not drop `--locked` to hide it.
- Container exits: check required token, paired Octopus credentials and valid settings.
- SQLite permission errors: check volume UID/GID mapping and restored ownership.
- HA unavailable: confirm its URL/network, token, stale flag and individual null fields.
- No Docker daemon or Compose plugin: static YAML tests cannot verify runtime; install
  prerequisites or use the local Python setup, and report the acceptance gap.
