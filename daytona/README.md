# Shared MCP servers on Daytona

Run the six Enterprise-Bench MCP servers **once** in a Daytona sandbox and point
any number of Harbor trials (any agent: `claude-code`, `codex`, …) or other
harnesses at them through **one URL with six paths**. No per-trial servers, no
local servers, no custom agent code.

```text
operator machine / CI
  shared_mcp_daytona.py snapshot | deploy | config | revoke | inspect | stop
                     │
                     ▼
server sandbox (private; from snapshot shared-mcp-servers-v2)
  Caddy :8080 ─┬─ /pm/mcp       → pm:8011
               ├─ /crm/mcp      → crm:8012
               ├─ /drive/mcp    → file-server:8013
               ├─ /support/mcp  → cs:8014
               ├─ /email/mcp    → mail-server:8015
               └─ /calendar/mcp → calendar-server:8016
                     ▲
  https://8080-<signed-token>.<proxy-domain>/<route>/mcp
                     │
  harbor run -c .daytona/job.yaml --mcp-config .daytona/mcp.json
    └─ trial sandboxes (from snapshot enterprise-bench-task-env-<digest12>)
  any other MCP client using the same mcp.json
```

| Path | Purpose |
|---|---|
| `shared_mcp_daytona.py` | Operator CLI: `snapshot`, `server-snapshot`, `deploy`, `inspect`, `config`, `revoke`, `stop`. Runs locally or in CI. |
| `shared_mcp_config.py` | Renders `.daytona/mcp.json` from the repo-root `mcp.json` (keys, descriptions, `type: http` kept; only URLs change). |
| `shared_mcp_smoke.py` | MCP `initialize`, `tools/list` and read-only `crm_describe` through the real URL; `--concurrency N`. |
| `deploy/` | What runs in the server sandbox: `docker-compose.yaml`, `Caddyfile`, `images.env`, `image-lock.json`. Generated, tracked. |
| `images/` | Build and publish the six server images; render `deploy/` from the image lock. |
| `tests/`, `images/tests/` | Offline tests (no cloud, Docker or model). |

## Snapshots

There are two Daytona snapshots, and both names are fixed by what is pinned in
this repo:

| Snapshot | Used by | Contents | Created by |
|---|---|---|---|
| `enterprise-bench-task-env-<digest12>` | every Harbor trial sandbox (where the agent runs) | `artifacts/base-image.zip` built as an image. `<digest12>` is the first 12 hex of that zip's digest in `dataset.toml`. Required: the task Dockerfiles start `FROM enterprise-bench/conversational-base:latest`, which is not on any registry. | `snapshot` |
| `shared-mcp-servers-v2` | the MCP server sandbox | `docker:28.3.3-dind` plus the `deploy/` files, built from `deploy/Dockerfile` | `server-snapshot` |

To see exactly which task snapshot this checkout uses, and whether it exists:

```bash
python daytona/shared_mcp_daytona.py snapshot --print
```

Snapshots stay until deleted, but Daytona deactivates ones that go unused for a
while. `snapshot`, `config` and `deploy` reactivate an inactive snapshot
automatically. If it is missing (deleted), `snapshot` rebuilds it from the pinned
zip. `--print` only reports and exits 1 when the snapshot isn't active.

Each run's `jobs/<job>/config.json` records the snapshot under
`environment.kwargs.snapshot_template_name`. When `base-image.zip` changes, the
name changes, so old runs keep pointing at the snapshot they actually used.

## Harbor run using Daytona

Prerequisites, all in `.env` (copy `.env.template`):

- `DAYTONA_API_KEY`: Daytona dashboard → Keys
- `ANTHROPIC_API_KEY`: or the key for whichever agent you run
- `OPENAI_API_KEY`: LLM judge

One-time setup:

```bash
make install                  # uv sync: Harbor 0.19 with Daytona support in .venv
source .venv/bin/activate     # `harbor` and `python` below come from .venv
python daytona/shared_mcp_daytona.py snapshot   # once per base-image version; no-op after
```

Before each run (or every 24 hours), refresh the signed URL. This writes
`.daytona/mcp.json` and `.daytona/job.yaml` (Daytona environment + task snapshot):

```bash
python daytona/shared_mcp_daytona.py config \
  --sandbox-name eb-shared-mcp --output .daytona/mcp.json
```

Then run Harbor:

```bash
harbor run \
  -c .daytona/job.yaml \
  -p tasks \
  -a claude-code -m claude-opus-4-8 \
  --mcp-config .daytona/mcp.json \
  --env-file .env \
  -k [REPETITIONS] -n [CONCURRENT] --jobs-dir [OUTPUT_DIR] --yes
```

- One task: `-p tasks/eng-l1-a`. A subset: `-p tasks -i eng-l1-a -i sales-l1-a`.
- Any Harbor agent works, e.g. `-a codex -m <model>` or `-a agents.<module>:<Class>`.
- Keep `--mcp-config` on the command line. With `-c`, Harbor replaces the agent
  section of the job config whenever `-a` is given (0.19 through 0.24), so
  `job.yaml` only carries the environment.
- Without `-c`, the equivalent flags are
  `-e daytona --ek snapshot_template_name=$(python daytona/shared_mcp_daytona.py snapshot --print | …)`.
  The job config is simpler.
- The global `harbor` from `uv tool` (0.17 here) has no Daytona support. Use the
  `.venv` one, or upgrade it with `uv tool install --upgrade 'harbor[daytona]'`.

## Deploy the MCP servers

```bash
python daytona/shared_mcp_daytona.py deploy \
  --sandbox-name eb-shared-mcp --manifest .daytona/deployment.json
```

This creates the private sandbox `eb-shared-mcp` from `shared-mcp-servers-v2`
with auto-stop off, or reuses it if it exists. It then:

1. uploads `deploy/`;
2. starts `dockerd` if it isn't running (Daytona doesn't run image entrypoints,
   so this also covers a restarted sandbox);
3. pulls the six digest-pinned images from GHCR, which are public so no login is
   needed, and starts them with Caddy;
4. waits until all six routes answer MCP `initialize` on `:8080`;
5. writes a non-secret manifest (sandbox id, repo commit, release, image digests,
   routes, start time) locally and in the sandbox.

Re-running `deploy` is safe, and `--dry-run` never contacts Daytona. If you
publish private images, set `GHCR_TOKEN` (a `read:packages` token); it travels as
an environment variable, never on a command line.

Check it, and look at the generated config, with:

```bash
python daytona/shared_mcp_daytona.py inspect --sandbox-name eb-shared-mcp
python daytona/shared_mcp_smoke.py --config .daytona/mcp.json --concurrency 4
```

`config` asks Daytona for a **signed preview URL** for port 8080, of the form
`https://8080-<token>.<proxy-domain>`, valid for at most 24 hours
(`--expires-in`). Every server in `.daytona/mcp.json` gets that origin plus its
route, with no headers, written with mode `0600` into the git-ignored
`.daytona/`. Without the signed token the router answers 401. Non-Harbor
harnesses can load the same file; it is a standard Claude-style `mcp.json` over
Streamable HTTP.

## After the run

```bash
python daytona/shared_mcp_daytona.py revoke \
  --sandbox-name eb-shared-mcp --config .daytona/mcp.json
```

- **Expiry.** A run longer than the URL's lifetime loses its servers. Keep runs
  under 24 hours, or run `config` again between runs.
- **Restarts.** After a sandbox restart, run `deploy` (it restarts the stack) and
  then `config`.
- **Shutdown.** Stop the deployment explicitly with `stop --sandbox-name
  eb-shared-mcp --yes` (add `--delete` to remove it). Harbor's trial cleanup
  never touches it.

## Operator runbook

`<MCP_SANDBOX_NAME>` is any name you choose for one MCP deployment (example:
`eb-shared-mcp`). Use the same name in every `deploy`, `config`, `inspect`,
`revoke`, and `stop` for that deployment. The `.daytona/` directory is
gitignored; `deploy` and `config` create it when you write paths under it.

| File | Created by | Purpose |
|---|---|---|
| `deployment.json` | `deploy --manifest …` | Non-secret record of sandbox id and image digests |
| `mcp.json` | `config --output …` | Signed MCP URLs for `--mcp-config` |
| `job.yaml` | `config` (default: next to `mcp.json`) | Harbor `-c`: Daytona task snapshot only |

**One-time (Daytona org):** `snapshot` (task env) → `server-snapshot` (if missing)
→ `deploy --sandbox-name <MCP_SANDBOX_NAME>`. **Before each run:** `config` →
`harbor run` with `-c .daytona/job.yaml` and `--mcp-config .daytona/mcp.json`.

**MCP sandbox stopped or restarted:** run `deploy` again (starts dockerd and the
stack). `config` alone does not start containers. Then `config` if the signed URL
expired or before a long run.

**Sandbox deleted:** `deploy` → `config` (new signed URL).

### When repo artifacts or images change

| What changed | What to do |
|---|---|
| `artifacts/data.zip` or `artifacts/mcp-servers.zip` (MCP data/code) | Full **image release** below, then `deploy` on every `<MCP_SANDBOX_NAME>` you use, then `config`. |
| `daytona/deploy/` only (compose, digests, Caddy) | Commit `deploy/`, `server-snapshot --replace` (optional if sandboxes already exist), `deploy` on each MCP sandbox, `config`. |
| `artifacts/base-image.zip` (agent trial image) | `python daytona/shared_mcp_daytona.py snapshot` only (new `enterprise-bench-task-env-<digest12>`). Re-run `config` so `job.yaml` picks up the new name. No MCP image rebuild unless data zip also changed. |
| MCP sandbox stop/start only | `deploy` → `config` (see above). |

### Image release (data or server code in GHCR images)

Server data and MCP code are **baked into the six GHCR images**, not loaded at
runtime from your laptop. After changing `data.zip` or `mcp-servers.zip`, run:

```bash
make validate

python daytona/images/build_images.py prepare --owner <ghcr-owner> --release <id>
python daytona/images/build_images.py build   --owner <ghcr-owner> --release <id> --push
python daytona/images/build_images.py publish --owner <ghcr-owner> --release <id>
python daytona/images/gen_deploy.py --image-lock .daytona/build/<id>/image-lock.json
```

Commit the updated `daytona/deploy/` (including `image-lock.json`). Then in
Daytona:

```bash
python daytona/shared_mcp_daytona.py server-snapshot --replace   # when deploy/ changed
python daytona/shared_mcp_daytona.py deploy \
  --sandbox-name <MCP_SANDBOX_NAME> \
  --manifest .daytona/deployment.json
python daytona/shared_mcp_daytona.py config \
  --sandbox-name <MCP_SANDBOX_NAME> \
  --output .daytona/mcp.json
```

Repeat `deploy` + `config` for each extra MCP sandbox (e.g. isolated parallel
agents). Make new GHCR packages public after their first push (package settings
→ Danger Zone → Change visibility) so `deploy` can pull without `GHCR_TOKEN`.

- `prepare` stages from the pinned zips and refuses archives that do not match
  `dataset.toml`.
- `build --push` needs `docker login ghcr.io` with `write:packages`.
- `gen_deploy.py` rewrites `deploy/`; `deploy` uploads the current tree to the
  sandbox and pulls **digests** from `images.env`.
- `server-snapshot` rebuilds `shared-mcp-servers-v2` from `deploy/Dockerfile`
  (dind + deploy files only). `deploy` always uploads the latest `deploy/` even
  if you skip `server-snapshot`.

When `artifacts/base-image.zip` changes, run `snapshot` again; the new task
snapshot name is derived automatically.

All runs you compare should use the same `deploy/image-lock.json` and task
snapshot.

## Caveats

- **Signed URLs end up in job outputs.** Harbor records MCP URLs in
  `jobs/*/config.json`. Revoke the URL, or let it expire, before sharing `jobs/`
  or uploading traces.
- **Read-only tasks only.** Mail and Calendar keep writes (drafts, labels, events)
  in process memory, so on a shared deployment one trial's writes are visible to
  every other trial. Redeploy between scored runs.
- **MCP only.** The server images don't include the REST twins (9001-9004).
- **Snapshots are per Daytona organization.** Another org runs `snapshot` and
  `server-snapshot` once, then `deploy`. The public images need no token.
- **Network isolation.** The signed URL protects the servers; it is not the
  default-deny agent egress policy required before public v2 scoring.

## Tests

```bash
uv run --extra dev pytest daytona/
```
