#!/usr/bin/env python3
"""Build & publish the six Enterprise-Bench MCP servers as self-contained GHCR
images, with code + per-service data baked in.

Three subcommands, each taking the SAME ``--owner`` and ``--release`` so the
same staged input flows end-to-end:

    build_images.py prepare  --owner OWNER --release v2-20261008-r1
    build_images.py build    --owner OWNER --release v2-20261008-r1 [--push]
    build_images.py publish  --owner OWNER --release v2-20261008-r1

* ``prepare`` — SHA256-verify both canonical zips, then stage six minimal build
  contexts under ``.daytona/build/<release>/contexts/<service>/`` and write
  a release ``manifest.json`` (archive hashes, source git commit, tag,
  timestamp, declared data identity). No Docker needed.

* ``build`` — ``docker buildx build --platform linux/amd64 --load`` each staged
  context. ``--push`` (opt-in only) switches ``--load`` to ``--push`` and tags
  the images for GHCR. Without ``--push`` nothing leaves the host and no mutable
  ``latest``/``v2`` tag is created.

* ``publish`` — AFTER a successful ``--push`` build, resolve each pushed image to
  an immutable ``@sha256:`` digest via ``docker buildx imagetools inspect`` and
  write an ``image-lock.json`` + ``images.env`` that the deployment renderer
  consumes. Never prints credentials; never logs in; registry auth is the
  operator's existing ``docker login`` session.

Safety:
  * ``--owner`` is required (no default namespace) and validated to a lowercase,
    shell-safe GHCR path token.
  * ``--release`` is required and validated to a safe tag token; it is treated as
    immutable — ``build --push`` refuses to overwrite an existing *remote* tag
    unless ``--replace`` is given (and warns loudly).
  * All Docker invocations go through ``subprocess`` with argument lists (no
    shell), so service names / owner / release cannot inject shell commands.
  * This tool itself NEVER runs a push without the explicit ``--push`` flag, and
    the harness/operator must perform the actual run; nothing is auto-executed
    against a registry here beyond read-only ``imagetools inspect`` in publish.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

import contract
from contract import IMAGE_ENV_BY_KEY, SERVICES
from staging import StagingError, stage_all_contexts, verify_zip

REPO_ROOT_MARKERS = ("artifacts", "dataset.toml", "daytona")

# GHCR owner: lowercase alnum + hyphen (GitHub user/org rules, lowercased for
# registry path). No slashes, no shell metacharacters.
_OWNER_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,38}$")
# Release/tag token: Docker tag charset, bounded.
_RELEASE_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")


class BuildError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Validation                                                                  #
# --------------------------------------------------------------------------- #
def validate_owner(owner: str) -> str:
    o = (owner or "").strip().lower()
    if not _OWNER_RE.match(o):
        raise BuildError(
            "Invalid --owner. Must be a lowercase GHCR namespace token "
            "(alnum + hyphen, <=39 chars). No default is assumed."
        )
    return o


def validate_release(release: str) -> str:
    r = (release or "").strip()
    if not _RELEASE_RE.match(r):
        raise BuildError(
            "Invalid --release. Use a safe tag token, e.g. v2-20261008-r1."
        )
    if r == "latest":
        raise BuildError(
            "Refusing the floating 'latest' tag. Use a release id such as v2; "
            "deployments pin the pushed digests in daytona/deploy/."
        )
    return r


def image_ref(owner: str, service: contract.ServiceSpec, release: str) -> str:
    """ghcr.io/<owner>/<image_leaf>:<release> — the only tag this tool creates."""
    return f"ghcr.io/{owner}/{service.image_leaf}:{release}"


# --------------------------------------------------------------------------- #
# Repo / path resolution                                                      #
# --------------------------------------------------------------------------- #
def find_repo_root(start: Optional[Path] = None) -> Path:
    here = (start or Path(__file__).resolve()).resolve()
    for parent in [here] + list(here.parents):
        if all((parent / m).exists() for m in REPO_ROOT_MARKERS):
            return parent
    raise BuildError("Could not locate the enterprise-bench repo root.")


def build_root(repo_root: Path, release: str) -> Path:
    return repo_root / ".daytona" / "build" / release


def contexts_root(repo_root: Path, release: str) -> Path:
    return build_root(repo_root, release) / "contexts"


# --------------------------------------------------------------------------- #
# Git metadata (best-effort; sanitized)                                       #
# --------------------------------------------------------------------------- #
def _git(repo_root: Path, *args: str) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True, text=True, check=True,
        )
        return out.stdout.strip()
    except Exception:
        return None


def git_metadata(repo_root: Path) -> Dict[str, Optional[str]]:
    return {
        "commit": _git(repo_root, "rev-parse", "HEAD"),
        "commit_date": _git(repo_root, "show", "-s", "--format=%cI", "HEAD"),
        "branch": _git(repo_root, "rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": "true" if _git(repo_root, "status", "--porcelain") else "false",
    }


# --------------------------------------------------------------------------- #
# prepare                                                                     #
# --------------------------------------------------------------------------- #
def cmd_prepare(args: argparse.Namespace) -> int:
    owner = validate_owner(args.owner)
    release = validate_release(args.release)
    repo_root = find_repo_root()

    mcp_zip = repo_root / contract.MCP_SERVERS_ZIP
    data_zip = repo_root / contract.DATA_ZIP
    try:
        pins = (
            contract.pinned_sha256(repo_root, contract.MCP_SERVERS_ZIP),
            contract.pinned_sha256(repo_root, contract.DATA_ZIP),
        )
    except KeyError as exc:
        raise BuildError(str(exc))

    mcp_sha = verify_zip(mcp_zip, pins[0])
    data_sha = verify_zip(data_zip, pins[1])
    print(f"[prepare] verified mcp-servers.zip sha256={mcp_sha}")
    print(f"[prepare] verified data.zip        sha256={data_sha}")

    broot = build_root(repo_root, release)
    ctx_root = contexts_root(repo_root, release)
    if ctx_root.exists():
        shutil.rmtree(ctx_root)
    staged = stage_all_contexts(
        mcp_zip, data_zip, ctx_root, SERVICES, base_image=contract.PYTHON_BASE_IMAGE,
        pins=pins,
    )

    git = git_metadata(repo_root)
    now = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()

    services_meta = []
    for service, ctx in staged:
        services_meta.append(
            {
                "key": service.key,
                "config_name": service.config_name,
                "image_leaf": service.image_leaf,
                "image_ref": image_ref(owner, service, release),
                "mcp_port": service.mcp_port,
                "route": service.route,
                "route_aliases": list(service.route_aliases),
                "data_subdirs": list(service.data_subdirs),
                "context": str(ctx.relative_to(repo_root)),
            }
        )

    manifest = {
        "name": "enterprise-bench-mcp-ghcr",
        "release": release,
        "owner": owner,
        "generated_at": now,
        "platform": "linux/amd64",
        "base_image": contract.PYTHON_BASE_IMAGE,
        "caddy_image": contract.CADDY_IMAGE,
        "source_archives": [
            {
                "name": "mcp-servers.zip",
                "path": "artifacts/mcp-servers.zip",
                "sha256": mcp_sha,
            },
            {
                "name": "data.zip",
                "path": "artifacts/data.zip",
                "sha256": data_sha,
            },
        ],
        "data_identity": {
            "declared": "packaged",
            "note": (
                "Data is the exact content of the SHA256-pinned data.zip bundle."
            ),
        },
        "source_git": git,
        "services": services_meta,
    }
    manifest_path = broot / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(f"[prepare] staged {len(staged)} contexts under {ctx_root.relative_to(repo_root)}")
    print(f"[prepare] wrote manifest {manifest_path.relative_to(repo_root)}")
    print("[prepare] next: build_images.py build --owner "
          f"{owner} --release {release} [--push]")
    return 0


# --------------------------------------------------------------------------- #
# build                                                                       #
# --------------------------------------------------------------------------- #
def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _run(cmd: List[str], dry_run: bool) -> None:
    printable = " ".join(cmd)
    print(f"[run] {printable}")
    if dry_run:
        return
    subprocess.run(cmd, check=True)


def _remote_tag_exists(ref: str) -> bool:
    """Read-only check whether a remote tag already resolves (no overwrite)."""
    try:
        out = subprocess.run(
            ["docker", "buildx", "imagetools", "inspect", ref],
            capture_output=True, text=True,
        )
        return out.returncode == 0
    except Exception:
        return False


def cmd_build(args: argparse.Namespace) -> int:
    owner = validate_owner(args.owner)
    release = validate_release(args.release)
    repo_root = find_repo_root()
    ctx_root = contexts_root(repo_root, release)

    if not ctx_root.is_dir():
        raise BuildError(
            f"No staged contexts for release '{release}'. Run `prepare` first."
        )
    if not args.dry_run and not _docker_available():
        raise BuildError("docker CLI not found. Install Docker/buildx or use --dry-run.")

    if args.push and not args.replace:
        # Immutable release: refuse to clobber an existing remote tag.
        clashes = [
            image_ref(owner, s, release)
            for s in SERVICES
            if _remote_tag_exists(image_ref(owner, s, release))
        ]
        if clashes:
            raise BuildError(
                "Refusing to overwrite existing remote tag(s) for an immutable "
                "release:\n  " + "\n  ".join(clashes) +
                "\nUse a new --release, or pass --replace to intentionally retag."
            )

    output_flag = "--push" if args.push else "--load"
    if args.push:
        print("[build] WARNING: --push will upload images to GHCR using your "
              "existing `docker login ghcr.io` session.")
    for service in SERVICES:
        ctx = ctx_root / service.key
        ref = image_ref(owner, service, release)
        cmd = [
            "docker", "buildx", "build",
            "--platform", "linux/amd64",
            "-f", str(ctx / "Dockerfile"),
            "-t", ref,
            "--build-arg", f"BASE_IMAGE={contract.PYTHON_BASE_IMAGE}",
            output_flag,
            str(ctx),
        ]
        _run(cmd, args.dry_run)

    print(f"[build] {'pushed' if args.push else 'built (local --load)'} "
          f"{len(SERVICES)} images for release {release}")
    if args.push:
        print(f"[build] next: build_images.py publish --owner {owner} --release {release}")
    return 0


# --------------------------------------------------------------------------- #
# publish (digest resolution + lock)                                          #
# --------------------------------------------------------------------------- #
def _resolve_digest(ref: str) -> str:
    """Resolve a pushed tag to its immutable repo digest via imagetools.

    Uses the robust JSON format and falls back to raw manifest-descriptor
    parsing. Never prints credentials.
    """
    # Preferred: structured descriptor digest.
    out = subprocess.run(
        ["docker", "buildx", "imagetools", "inspect", ref,
         "--format", "{{json .Manifest}}"],
        capture_output=True, text=True,
    )
    if out.returncode == 0 and out.stdout.strip():
        try:
            desc = json.loads(out.stdout)
            digest = desc.get("digest")
            if isinstance(digest, str) and digest.startswith("sha256:"):
                return digest
        except json.JSONDecodeError:
            pass
    # Fallback: parse the human output for "Digest: sha256:...".
    out2 = subprocess.run(
        ["docker", "buildx", "imagetools", "inspect", ref],
        capture_output=True, text=True,
    )
    if out2.returncode == 0:
        m = re.search(r"Digest:\s+(sha256:[0-9a-f]{64})", out2.stdout)
        if m:
            return m.group(1)
    raise BuildError(f"Could not resolve immutable digest for {ref} (is it pushed?).")


def cmd_publish(args: argparse.Namespace) -> int:
    owner = validate_owner(args.owner)
    release = validate_release(args.release)
    repo_root = find_repo_root()
    broot = build_root(repo_root, release)
    if not broot.is_dir():
        raise BuildError(f"No build dir for release '{release}'. Run prepare/build first.")
    if not args.dry_run and not _docker_available():
        raise BuildError("docker CLI not found. Install Docker/buildx or use --dry-run.")

    repo_prefix = f"ghcr.io/{owner}/"
    lock: Dict[str, Dict[str, str]] = {}
    env_lines: List[str] = [
        "# GENERATED by build_images.py publish — digest-pinned image refs.",
        "# Non-secret deployment lock. Consumed by the generated docker-compose.",
        f"# release={release} owner={owner}",
    ]
    for service in SERVICES:
        ref = image_ref(owner, service, release)
        if args.dry_run:
            digest = "sha256:" + "0" * 64
        else:
            digest = _resolve_digest(ref)
        pinned = f"{repo_prefix}{service.image_leaf}@{digest}"
        lock[service.key] = {
            "image_leaf": service.image_leaf,
            "tag_ref": ref,
            "digest": digest,
            "pinned_ref": pinned,
        }
        env_lines.append(f"{IMAGE_ENV_BY_KEY[service.key]}={pinned}")
        print(f"[publish] {service.key}: {pinned}")

    lock_path = broot / "image-lock.json"
    lock_path.write_text(
        json.dumps(
            {"release": release, "owner": owner, "platform": "linux/amd64",
             "images": lock},
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    env_path = broot / "images.env"
    env_path.write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    print(f"[publish] wrote {lock_path.relative_to(repo_root)}")
    print(f"[publish] wrote {env_path.relative_to(repo_root)}")
    print("[publish] next: gen_deploy.py --release "
          f"{release} --image-lock {lock_path.relative_to(repo_root)}")
    return 0


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #
def _add_common(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--owner", required=True, help="GHCR namespace (required, no default).")
    sp.add_argument("--release", required=True, help="Immutable release id, e.g. v2-20261008-r1.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="build_images.py",
        description="Stage, build and publish Enterprise-Bench MCP GHCR images.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sp_prepare = sub.add_parser("prepare", help="Verify zips + stage build contexts + manifest.")
    _add_common(sp_prepare)
    sp_prepare.set_defaults(func=cmd_prepare)

    sp_build = sub.add_parser("build", help="docker buildx build each context (--load default).")
    _add_common(sp_build)
    sp_build.add_argument("--push", action="store_true",
                          help="Opt-in: push to GHCR instead of local --load.")
    sp_build.add_argument("--replace", action="store_true",
                          help="With --push: allow overwriting an existing remote tag (warns).")
    sp_build.add_argument("--dry-run", action="store_true",
                          help="Print docker commands without executing them.")
    sp_build.set_defaults(func=cmd_build)

    sp_publish = sub.add_parser("publish", help="Resolve pushed digests + write lock/env.")
    _add_common(sp_publish)
    sp_publish.add_argument("--dry-run", action="store_true",
                            help="Emit placeholder digests without contacting the registry.")
    sp_publish.set_defaults(func=cmd_publish)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (BuildError, StagingError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print(f"error: docker command failed (exit {exc.returncode}).", file=sys.stderr)
        return exc.returncode or 1


if __name__ == "__main__":
    raise SystemExit(main())
