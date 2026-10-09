# Shared MCP servers on Daytona

Run the six Enterprise-Bench MCP servers **once** in a Daytona sandbox and point
any number of Harbor trials (any agent: `claude-code`, `codex`, …) or other
harnesses at them through **one URL with six paths**. No per-trial servers, no
local servers, no custom agent code.

```text
operator machine / CI
  shared_mcp_daytona.py server-snapshot | deploy | config | revoke | inspect | stop
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
  harbor run -e environments.daytona_env:DaytonaEnvironment
             --mcp-config .daytona/mcp.json
    └─ trial sandboxes (GHCR conversational-base image per task Dockerfile)
  any other MCP client using the same mcp.json
```

| Path | Purpose |
|---|---|
| `shared_mcp_daytona.py` | Operator CLI: `server-snapshot`, `deploy`, `inspect`, `config`, `revoke`, `stop`. Runs locally or in CI. |
| `shared_mcp_config.py` | Renders `.daytona/mcp.json` from the repo-root `mcp.json` (keys, descriptions, `type: http` kept; only URLs change). |
| `shared_mcp_smoke.py` | MCP `initialize`, `tools/list` and read-only `crm_describe` through the real URL; `--concurrency N`. |
| `environments/daytona_env.py` | Harbor environment: GHCR task image + submit API on :8000 for conversational tasks. |
| `deploy/` | What runs in the server sandbox: `docker-compose.yaml`, `Caddyfile`, `images.env`, `image-lock.json`. Generated, tracked. |
| `images/` | Build and publish the six server images; render `deploy/` from the image lock. |
| `tests/`, `images/tests/` | Offline tests (no cloud, Docker or model). |

## Snapshots

| Snapshot | Used by | Contents | Created by |
|---|---|---|---|
| `shared-mcp-servers-v2` | the MCP server sandbox | `docker:28.3.3-dind` plus the `deploy/` files, built from `deploy/Dockerfile` | `server-snapshot` |

The optional `snapshot` command still builds `enterprise-bench-task-env-<digest12>` from
`artifacts/base-image.zip` for legacy workflows; **Harbor trials on Daytona should use
the GHCR conversational-base image** via `DaytonaEnvironment` instead.

## Harbor run using Daytona

Prerequisites, all in `.env` (copy `.env.template`):

- `DAYTONA_API_KEY`: Daytona dashboard → Keys
- `ANTHROPIC_API_KEY`: or the key for whichever agent you run
- `OPENAI_API_KEY`: LLM judge
- `GHCR_OWNER`: GitHub user/org that hosts `ghcr.io/<owner>/conversational-base` (or set `EB_CONVERSATIONAL_BASE_IMAGE`)

One-time setup:

```bash
make install                  # uv sync: Harbor with Daytona support in .venv
source .venv/bin/activate
make setup                    # extracts images/conversational-base/ from artifacts/base-image.zip
GHCR_OWNER=your-github-user ./daytona/images/build_conversational_base.sh   # push linux/amd64 base
python daytona/shared_mcp_daytona.py server-snapshot   # once; no-op if already active
python daytona/shared_mcp_daytona.py deploy \
  --sandbox-name eb-shared-mcp --manifest .daytona/deployment.json
```

Before each run (or every 24 hours), refresh the signed MCP URL:

```bash
python daytona/shared_mcp_daytona.py config \
  --sandbox-name eb-shared-mcp --output .daytona/mcp.json
```

Then run Harbor (any harness that supports Harbor's environment import path):

```bash
export GHCR_OWNER=your-github-user

harbor run \
  -e environments.daytona_env:DaytonaEnvironment \
  -p tasks \
  -a codex -m gpt-6.1-sol \
  --mcp-config .daytona/mcp.json \
  --env-file .env \
  -k [REPETITIONS] -n [CONCURRENT] --jobs-dir [OUTPUT_DIR] --yes
```

- One task: `-p tasks/eng-l1-a`. A subset: `-p tasks -i eng-l1-a -i sales-l1-a`.
- Task sandboxes pull `ghcr.io/$GHCR_OWNER/conversational-base:latest` (or the image in the task Dockerfile). `DaytonaEnvironment` rewrites `FROM enterprise-bench/conversational-base` to that GHCR ref and starts the submit API on port 8000 (Daytona does not run image ENTRYPOINT).
- Optional: `EB_CONVERSATIONAL_BASE_IMAGE` (full ref), `CONVERSATIONAL_BASE_TAG` (default `latest`).
- The global `harbor` from `uv tool` may lack Daytona support. Use the `.venv` binary or `uv tool install 'harbor[daytona]'`.

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

**One-time (Daytona org):** `server-snapshot` (if missing) → `deploy --sandbox-name <MCP_SANDBOX_NAME>`.
**Before each run:** `config` → `harbor run` with `DaytonaEnvironment` and `--mcp-config .daytona/mcp.json`.

**MCP sandbox stopped or restarted:** run `deploy` again (starts dockerd and the
stack). `config` alone does not start containers. Then `config` if the signed URL
expired or before a long run.

**Sandbox deleted:** `deploy` → `config` (new signed URL).

### When repo artifacts or images change

| What changed | What to do |
|---|---|
| `artifacts/data.zip` or `artifacts/mcp-servers.zip` (MCP data/code) | Full **image release** below, then `deploy` on every `<MCP_SANDBOX_NAME>` you use, then `config`. |
| `daytona/deploy/` only (compose, digests, Caddy) | Commit `deploy/`, `server-snapshot --replace` (optional if sandboxes already exist), `deploy` on each MCP sandbox, `config`. |
| `artifacts/base-image.zip` (task runtime) | `make setup`, re-run `daytona/images/build_conversational_base.sh`, then re-run trials (Daytona pulls the new GHCR tag). |
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
