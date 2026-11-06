#!/usr/bin/env python3
"""Operator CLI for the shared Daytona MCP deployment (GHCR images + Caddy).

Runs on the operator's machine or in CI, never inside the server sandbox.

  snapshot build the Daytona snapshot every task trial starts from,
           enterprise-bench-task-env-<12 hex of artifacts/base-image.zip digest>,
           in Daytona's cloud from the pinned base-image.zip. No-op if it exists.
  server-snapshot
           build shared-mcp-servers-v2 in Daytona from daytona/deploy/Dockerfile
           (docker:28.3.3-dind + the deploy files).
  deploy   create (or reuse) a private sandbox from shared-mcp-servers-v2,
           upload daytona/deploy/, start dockerd, pull the digest-pinned images,
           start the six servers + Caddy on :8080, wait for every route to
           answer MCP initialize, record a non-secret manifest.
  inspect  show sandbox state, Compose status and the recorded manifest.
  config   create a signed preview URL for :8080 and render .daytona/mcp.json
           (six URLs on that one origin, no headers) from the repo-root mcp.json.
  revoke   expire the signed URL used by a generated config.
  stop     stop (or --delete) the deployment. Never tied to Harbor cleanup.

Reads credentials (``DAYTONA_API_KEY``, optional ``GHCR_TOKEN``) from the
environment, loading the repo-root ``.env`` first if present (``--env-file``
overrides). ``--help`` and ``deploy --dry-run`` never contact Daytona.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
import tomllib
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shared_mcp_config as cfg  # noqa: E402

DAYTONA_DIR = Path(__file__).resolve().parent
REPO_ROOT = DAYTONA_DIR.parent
DEPLOY_DIR = DAYTONA_DIR / "deploy"
DEPLOY_FILES = ("docker-compose.yaml", "Caddyfile", "images.env")
SERVER_SNAPSHOT = "shared-mcp-servers-v2"

BASE_IMAGE_ZIP = "artifacts/base-image.zip"
TASK_SNAPSHOT_PREFIX = "enterprise-bench-task-env-"
# Matches every task.toml ([environment] cpus = 2, memory_mb = 2048); disk in GB.
TASK_SNAPSHOT_RESOURCES = {"cpu": 2, "memory": 2, "disk": 3}

REMOTE_ROOT = "/srv/enterprise-bench"
REMOTE_MANIFEST = f"{REMOTE_ROOT}/deployment.json"
COMPOSE_BASE = "docker compose -p enterprise-bench-mcp"
COMPOSE = f"{COMPOSE_BASE} --env-file images.env"
LABELS = {"purpose": "enterprise-bench-shared-mcp"}
MAX_EXPIRY = 24 * 3600

# Daytona does not run an image's entrypoint, so start dockerd when it isn't up.
START_DOCKERD = (
    "docker info >/dev/null 2>&1 || "
    "(nohup dockerd-entrypoint.sh dockerd > /var/log/dockerd.log 2>&1 &)"
)
SERVER_SNAPSHOT_RESOURCES = {"cpu": 4, "memory": 8, "disk": 10}
MANIFEST_VERSION = 2

INITIALIZE = json.dumps({
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-03-26", "capabilities": {},
               "clientInfo": {"name": "deploy-probe", "version": "1"}},
})  # fmt: skip


class DeployError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Local inputs and provenance                                                 #
# --------------------------------------------------------------------------- #
def load_lock(deploy_dir: Path = DEPLOY_DIR) -> dict[str, Any]:
    """Read the tracked image lock and check images.env matches it."""
    for name in (*DEPLOY_FILES, "image-lock.json"):
        if not (deploy_dir / name).is_file():
            raise DeployError(f"Missing {deploy_dir / name}; run daytona/images/gen_deploy.py.")
    lock = json.loads((deploy_dir / "image-lock.json").read_text(encoding="utf-8"))
    refs = {entry["pinned_ref"] for entry in lock.get("images", {}).values()}
    env_refs = {
        line.split("=", 1)[1]
        for line in (deploy_dir / "images.env").read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.startswith("#")
    }
    if len(refs) != 6 or refs != env_refs:
        raise DeployError("images.env does not match image-lock.json; regenerate deploy/.")
    return lock


def pinned_digest(rel_path: str) -> str:
    """The ``sha256:...`` digest dataset.toml records for ``rel_path``."""
    manifest = tomllib.loads((REPO_ROOT / "dataset.toml").read_text(encoding="utf-8"))
    for entry in manifest.get("files", []):
        if entry.get("path") == rel_path and str(entry.get("digest", "")).startswith("sha256:"):
            return entry["digest"]
    raise DeployError(f"dataset.toml has no sha256 digest for {rel_path}.")


def task_snapshot_name() -> str:
    """Snapshot name derived from the pinned base-image.zip digest."""
    return TASK_SNAPSHOT_PREFIX + pinned_digest(BASE_IMAGE_ZIP).split(":", 1)[1][:12]


def extract_base_image(dest: Path) -> Path:
    """Verify base-image.zip against dataset.toml, extract it, return its Dockerfile."""
    archive = REPO_ROOT / BASE_IMAGE_ZIP
    expected = pinned_digest(BASE_IMAGE_ZIP)
    actual = "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()
    if actual != expected:
        raise DeployError(f"{BASE_IMAGE_ZIP} does not match its dataset.toml digest.")
    with zipfile.ZipFile(archive) as zf:
        for member in zf.infolist():
            path = PurePosixPath(member.filename)
            if path.is_absolute() or ".." in path.parts:
                raise DeployError(f"Unsafe path in {BASE_IMAGE_ZIP}: {member.filename}")
        zf.extractall(dest)
    dockerfile = dest / "base-image" / "Dockerfile"
    if not dockerfile.is_file():
        raise DeployError(f"{BASE_IMAGE_ZIP} has no base-image/Dockerfile.")
    return dockerfile


def git_commit() -> dict[str, Any]:
    def git(*args: str) -> str | None:
        try:
            out = subprocess.run(["git", "-C", str(REPO_ROOT), *args],
                                 capture_output=True, text=True, check=True)
            return out.stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return None
    return {"commit": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain"))}


def build_manifest(*, sandbox_id: str, sandbox_name: str, lock: dict[str, Any],
                   started_at: str) -> dict[str, Any]:
    return {
        "version": MANIFEST_VERSION,
        "sandbox_id": sandbox_id,
        "sandbox_name": sandbox_name,
        "repo": git_commit(),
        "release": lock.get("release"),
        "owner": lock.get("owner"),
        "images": {key: entry["pinned_ref"] for key, entry in sorted(lock["images"].items())},
        "router_port": cfg.ROUTER_PORT,
        "routes": dict(cfg.SERVICE_ROUTES),
        "started_at": started_at,
    }


def same_release(a: dict[str, Any] | None, b: dict[str, Any]) -> bool:
    return bool(a) and a.get("images") == b.get("images")


# --------------------------------------------------------------------------- #
# Remote helpers                                                              #
# --------------------------------------------------------------------------- #
def daytona_client():
    try:
        from daytona import Daytona
    except ImportError:
        raise DeployError("Daytona SDK missing; run `uv sync` (or `make install`).")
    return Daytona()


def find_sandbox(client, name: str):
    from daytona import DaytonaNotFoundError

    try:
        return client.get(name)
    except DaytonaNotFoundError:
        return None


def require_sandbox(client, name: str):
    sandbox = find_sandbox(client, name)
    if sandbox is None:
        raise DeployError(f"No sandbox named '{name}'.")
    return sandbox


def state_of(sandbox) -> str:
    return str(getattr(sandbox.state, "value", sandbox.state)).lower()


def run(sandbox, command: str, *, timeout: int = 120, env: dict | None = None) -> str:
    response = sandbox.process.exec(command, cwd=REMOTE_ROOT, env=env, timeout=timeout)
    if response.exit_code != 0:
        tail = "\n".join((response.result or "").splitlines()[-30:])
        raise DeployError(f"Remote command failed (exit {response.exit_code}): {command}\n{tail}")
    return response.result or ""


def read_remote_manifest(sandbox) -> dict[str, Any] | None:
    out = run(sandbox, f"cat {REMOTE_MANIFEST} 2>/dev/null || true", timeout=30)
    try:
        return json.loads(out) if out.strip() else None
    except json.JSONDecodeError:
        return None


def probe_script() -> str:
    """POSIX sh run inside the sandbox: MCP initialize on every router route."""
    routes = " ".join(cfg.SERVICE_ROUTES.values())
    return (
        "fail=0; "
        f"for r in {routes}; do wget -q -T 5 -O- --header='Content-Type: application/json' "
        "--header='Accept: application/json, text/event-stream' "
        f"--post-data={shlex.quote(INITIALIZE)} http://127.0.0.1:{cfg.ROUTER_PORT}$r/mcp "
        "2>/dev/null | grep -q serverInfo || { echo $r; fail=1; }; done; exit $fail"
    )


def wait_ready(sandbox, timeout_s: int = 300) -> None:
    deadline = time.monotonic() + timeout_s
    last = ""
    while time.monotonic() < deadline:
        response = sandbox.process.exec(probe_script(), cwd=REMOTE_ROOT, timeout=120)
        if response.exit_code == 0:
            print(f"All six routes answer MCP initialize on :{cfg.ROUTER_PORT} in the sandbox.")
            return
        last = " ".join((response.result or "").split())
        time.sleep(5)
    raise DeployError(f"Routes not ready after {timeout_s}s; failing: {last}")


# --------------------------------------------------------------------------- #
# Subcommands                                                                 #
# --------------------------------------------------------------------------- #
def cmd_deploy(args: argparse.Namespace) -> int:
    lock = load_lock()
    print(f"Release {lock.get('release')} (owner {lock.get('owner')}): 6 digest-pinned images.")
    if args.dry_run:
        print(f"Dry run: would deploy to sandbox '{args.sandbox_name}' from snapshot "
              f"'{args.snapshot}' and write {args.manifest}.")
        return 0

    from daytona import CreateSandboxFromSnapshotParams

    client = daytona_client()
    sandbox = find_sandbox(client, args.sandbox_name)
    if sandbox is None:
        reactivate_if_inactive(client, args.snapshot)
        print(f"Creating sandbox '{args.sandbox_name}' from snapshot '{args.snapshot}' ...")
        sandbox = client.create(
            CreateSandboxFromSnapshotParams(
                name=args.sandbox_name, snapshot=args.snapshot, labels=LABELS,
                public=False, auto_stop_interval=args.auto_stop,
            ),
            timeout=600,
        )
    else:
        print(f"Reusing sandbox '{args.sandbox_name}' ({sandbox.id}).")
        if state_of(sandbox) != "started":
            print(f"Starting sandbox ({state_of(sandbox)}) ...")
            sandbox.start(timeout=600)
    if sandbox.public:
        raise DeployError("Sandbox is public; refusing to deploy. Use a private sandbox.")

    sandbox.process.exec(f"mkdir -p {REMOTE_ROOT}", timeout=30)
    run(sandbox, START_DOCKERD, timeout=60)
    wait_for_docker(sandbox)
    run(sandbox, "docker compose version", timeout=60)
    existing = read_remote_manifest(sandbox)
    wanted = build_manifest(sandbox_id=sandbox.id, sandbox_name=args.sandbox_name,
                            lock=lock, started_at="")
    if existing and not same_release(existing, wanted):
        print(f"Replacing release {existing.get('release')} with {lock.get('release')}.")

    for name in DEPLOY_FILES:
        sandbox.fs.upload_file(str(DEPLOY_DIR / name), f"{REMOTE_ROOT}/{name}")
    print(f"Uploaded {', '.join(DEPLOY_FILES)}.")

    token = os.environ.get(args.ghcr_token_env) if args.ghcr_token_env else None
    if token:
        user = args.ghcr_user or lock.get("owner") or ""
        run(sandbox, 'printf %s "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" '
                     "--password-stdin >/dev/null",
            env={"GHCR_TOKEN": token, "GHCR_USER": user}, timeout=60)
        print("Logged in to ghcr.io inside the sandbox.")

    print("Starting the stack (pulling only images the sandbox lacks) ...")
    start_stack(sandbox, timeout=args.pull_timeout)

    manifest = (
        existing if same_release(existing, wanted)
        else dict(wanted, started_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
    )
    sandbox.fs.upload_file(json.dumps(manifest, indent=2).encode(), REMOTE_MANIFEST)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Deployed. Sandbox id {sandbox.id}; manifest written to {args.manifest}.")
    print("Next: `config` to create the signed URL and mcp.json, then shared_mcp_smoke.py.")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    sandbox = require_sandbox(daytona_client(), args.sandbox_name)
    print(f"Sandbox {sandbox.id}: state={state_of(sandbox)} public={sandbox.public} "
          f"auto_stop={getattr(sandbox, 'auto_stop_interval', '?')}m")
    if state_of(sandbox) != "started":
        return 1
    print(run(sandbox, f"{COMPOSE} ps --format '{{{{.Service}}}}: {{{{.Status}}}}'",
              timeout=60).rstrip())
    remote = read_remote_manifest(sandbox)
    print("Remote manifest:", json.dumps(remote, indent=2) if remote else "none")
    if remote and not same_release(remote, build_manifest(
        sandbox_id="", sandbox_name="", lock=load_lock(), started_at=""
    )):
        print("WARNING: deployed images differ from daytona/deploy/; run deploy to update.")
        return 1
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    if not 1 <= args.expires_in <= MAX_EXPIRY:
        raise DeployError(f"--expires-in must be between 1 and {MAX_EXPIRY} seconds (24h).")
    template = cfg.load_template(cfg.find_template(args.template))
    sandbox = require_sandbox(daytona_client(), args.sandbox_name)
    if state_of(sandbox) != "started":
        raise DeployError(f"Sandbox is {state_of(sandbox)}; start it (deploy reuses it).")
    if read_remote_manifest(sandbox) is None:
        raise DeployError("Sandbox has no deployment manifest; run deploy first.")

    signed = sandbox.create_signed_preview_url(cfg.ROUTER_PORT, expires_in_seconds=args.expires_in)
    if not getattr(signed, "url", None):
        raise DeployError(f"Daytona returned no signed URL for port {cfg.ROUTER_PORT}.")
    config = cfg.render_config(template, signed.url)
    cfg.write_config(config, args.output)
    expires = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=args.expires_in)
    print(f"Wrote {args.output} (6 servers, mode 0600). Signed URL expires "
          f"{expires.isoformat(timespec='minutes')}; revoke it with `revoke` when done.")
    return 0


def snapshot_state(client, name: str) -> str | None:
    """Lower-case snapshot state, or None if the snapshot doesn't exist."""
    try:
        snapshot = client.snapshot.get(name)
    except Exception as exc:
        if exc.__class__.__name__ == "DaytonaNotFoundError" or "not found" in str(exc).lower():
            return None
        raise
    return str(getattr(snapshot.state, "value", snapshot.state)).lower()


def reactivate_if_inactive(client, name: str, timeout_s: int = 600) -> str | None:
    """Daytona deactivates snapshots left unused for a while; turn one back on."""
    state = snapshot_state(client, name)
    if state != "inactive":
        return state
    print(f"Snapshot {name} is inactive; reactivating ...")
    client.snapshot.activate(name)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        state = snapshot_state(client, name)
        if state not in ("inactive", "pending", "pulling", "building", "snapshotting"):
            return state
        time.sleep(5)
    raise DeployError(f"Snapshot {name} did not become active within {timeout_s}s.")


def wait_for_docker(sandbox, timeout_s: int = 300) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if sandbox.process.exec("docker info >/dev/null 2>&1", timeout=30).exit_code == 0:
            return
        time.sleep(5)
    raise DeployError("Docker daemon did not come up in the sandbox.")


def start_stack(sandbox, timeout: int = 900) -> None:
    """Start the stack, pulling only the pinned images the sandbox lacks."""
    run(sandbox, f"{COMPOSE} up -d --pull missing --remove-orphans", timeout=timeout)
    wait_ready(sandbox)


def cmd_server_snapshot(args: argparse.Namespace) -> int:
    load_lock()
    client = daytona_client()
    state = snapshot_state(client, args.name)
    print(f"Server snapshot: {args.name} ({state or 'missing'})")
    if state is not None:
        if not args.replace:
            raise DeployError(f"Snapshot {args.name} already exists; pass --replace.")
        client.snapshot.delete(client.snapshot.get(args.name))
        while snapshot_state(client, args.name) is not None:
            time.sleep(5)

    from daytona import CreateSnapshotParams, Image, Resources

    print(f"Building {args.name} in Daytona from daytona/deploy/Dockerfile ...")
    client.snapshot.create(
        CreateSnapshotParams(
            name=args.name,
            image=Image.from_dockerfile(DEPLOY_DIR / "Dockerfile"),
            resources=Resources(**SERVER_SNAPSHOT_RESOURCES),
        ),
        on_logs=lambda line: print(f"  {line.rstrip()}"),
        timeout=args.timeout,
    )
    state = snapshot_state(client, args.name)
    if state != "active":
        raise DeployError(f"Snapshot {args.name} finished in state '{state}'.")
    print(f"Built {args.name}. Deploy with: deploy --sandbox-name <name> --manifest <path>")
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    name = task_snapshot_name()
    client = daytona_client()
    state = snapshot_state(client, name)
    print(f"Task snapshot: {name} ({state or 'missing'})")
    if args.print_only:
        return 0 if state == "active" else 1
    state = reactivate_if_inactive(client, name)
    if state == "active":
        print("Already built; nothing to do.")
        return 0
    if state is not None:
        raise DeployError(
            f"Snapshot {name} is in state '{state}'. Delete it "
            f"(`daytona snapshot delete {name}`) and re-run, or wait if it is still building."
        )

    from daytona import CreateSnapshotParams, Image, Resources

    with tempfile.TemporaryDirectory() as tmp:
        dockerfile = extract_base_image(Path(tmp))
        print(f"Building {name} in Daytona from {BASE_IMAGE_ZIP} (a few minutes) ...")
        client.snapshot.create(
            CreateSnapshotParams(
                name=name,
                image=Image.from_dockerfile(dockerfile),
                resources=Resources(**TASK_SNAPSHOT_RESOURCES),
            ),
            on_logs=lambda line: print(f"  {line.rstrip()}"),
            timeout=args.timeout,
        )
    state = snapshot_state(client, name)
    if state != "active":
        raise DeployError(f"Snapshot {name} finished in state '{state}'.")
    print(f"Built {name}.")
    return 0


def cmd_revoke(args: argparse.Namespace) -> int:
    token = cfg.config_token(cfg.load_remote_config(args.config))
    sandbox = require_sandbox(daytona_client(), args.sandbox_name)
    sandbox.expire_signed_preview_url(cfg.ROUTER_PORT, token)
    print(f"Signed URL in {args.config} expired.")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    if not args.yes:
        raise DeployError("Refusing without --yes (dependent runs will lose their servers).")
    client = daytona_client()
    sandbox = require_sandbox(client, args.sandbox_name)
    if args.delete:
        client.delete(sandbox, timeout=300)
        print(f"Deleted sandbox {sandbox.id}.")
    else:
        sandbox.stop(timeout=300)
        print(f"Stopped sandbox {sandbox.id}. After a restart, re-run deploy and config.")
    return 0


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,62}$")


def _sandbox_name(value: str) -> str:
    if not _NAME_RE.match(value):
        raise argparse.ArgumentTypeError("invalid sandbox name")
    return value


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="shared_mcp_daytona.py",
                                description=__doc__.split("\n\n")[0])
    p.add_argument("--env-file", type=Path, default=REPO_ROOT / ".env",
                   help="Dotenv file to load before running (default: repo-root .env, "
                        "skipped if missing; existing environment variables win).")
    sub = p.add_subparsers(dest="command", required=True)

    def with_name(sp):
        sp.add_argument("--sandbox-name", required=True, type=_sandbox_name)
        return sp

    t = sub.add_parser("snapshot", help="Build the task snapshot (no-op if it exists).")
    t.add_argument("--print", dest="print_only", action="store_true",
                   help="Only show the snapshot name and state; exit 1 if not active.")
    t.add_argument("--timeout", type=float, default=0, help="Build timeout in seconds (0=none).")
    t.set_defaults(func=cmd_snapshot)

    ss = sub.add_parser("server-snapshot",
                        help="Build the MCP server sandbox snapshot from daytona/deploy/.")
    ss.add_argument("--name", default=SERVER_SNAPSHOT)
    ss.add_argument("--replace", action="store_true", help="Replace an existing snapshot.")
    ss.add_argument("--timeout", type=float, default=0, help="Build timeout in seconds (0=none).")
    ss.set_defaults(func=cmd_server_snapshot)

    d = with_name(sub.add_parser("deploy", help="Deploy the GHCR + Caddy stack."))
    d.add_argument("--snapshot", default=SERVER_SNAPSHOT,
                   help=f"Server sandbox snapshot (default: {SERVER_SNAPSHOT}, images preloaded).")
    d.add_argument("--auto-stop", type=int, default=0, help="Minutes idle before stop (0=never).")
    d.add_argument("--pull-timeout", type=int, default=900)
    d.add_argument("--ghcr-token-env", default="GHCR_TOKEN",
                   help="Env var holding a read:packages token for private images.")
    d.add_argument("--ghcr-user", help="GHCR username (default: image owner).")
    d.add_argument("--manifest", type=Path, required=True, help="Local non-secret manifest path.")
    d.add_argument("--dry-run", action="store_true", help="Check inputs only; no Daytona calls.")
    d.set_defaults(func=cmd_deploy)

    i = with_name(sub.add_parser("inspect", help="Show sandbox, Compose and manifest status."))
    i.set_defaults(func=cmd_inspect)

    c = with_name(sub.add_parser("config", help="Create the signed URL and render mcp.json."))
    c.add_argument("--template", help="mcp.json template (default: repo-root mcp.json).")
    c.add_argument("--output", type=Path, required=True)
    c.add_argument("--expires-in", type=int, default=MAX_EXPIRY,
                   help="Signed URL lifetime in seconds (max 86400).")
    c.set_defaults(func=cmd_config)

    r = with_name(sub.add_parser("revoke", help="Expire the signed URL in a generated config."))
    r.add_argument("--config", type=Path, required=True)
    r.set_defaults(func=cmd_revoke)

    s = with_name(sub.add_parser("stop", help="Stop or delete the deployment."))
    s.add_argument("--delete", action="store_true")
    s.add_argument("--yes", action="store_true")
    s.set_defaults(func=cmd_stop)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.env_file.is_file():
        from dotenv import load_dotenv

        load_dotenv(args.env_file, override=False)
    try:
        return args.func(args)
    except (DeployError, cfg.ConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
