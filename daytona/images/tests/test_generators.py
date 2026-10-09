#!/usr/bin/env python3
"""Offline tests for gen_deploy (Caddyfile + compose + images.env), build_images
validation and the dataset.toml pins."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import build_images  # noqa: E402
import contract  # noqa: E402
import gen_deploy  # noqa: E402


@contextlib.contextmanager
def _quiet():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        yield buf


# --------------------------------------------------------------------------- #
# gen_deploy                                                                  #
# --------------------------------------------------------------------------- #
class CaddyfileTests(unittest.TestCase):
    def setUp(self):
        self.caddy = gen_deploy.render_caddyfile()

    def test_listens_on_8080(self):
        self.assertIn(":8080 {", self.caddy)

    def test_each_service_routed_to_upstream(self):
        for s in contract.SERVICES:
            self.assertIn(f"handle_path {s.route}/*", self.caddy)
            self.assertIn(f"reverse_proxy {s.key}:{s.mcp_port}", self.caddy)
            for alias in s.route_aliases:
                self.assertIn(f"handle_path {alias}/*", self.caddy)

    def test_streaming_and_host_header(self):
        self.assertIn("flush_interval -1", self.caddy)
        self.assertIn("header_up Host pm:8011", self.caddy)

    def test_rewrites_to_mcp(self):
        # Rewrite to the FIXED single MCP route. handle_path has already
        # stripped the "<prefix>" from "<prefix>/mcp", so the residual path is
        # "/mcp"; rewriting to a bare "/mcp" (NOT "/mcp{path}") avoids proxying
        # the double "/mcp/mcp" that the upstream FastMCP server 404s.
        self.assertIn("rewrite * /mcp", self.caddy)
        # Regression guard: the buggy "{path}" re-append must not come back.
        self.assertNotIn("rewrite * /mcp{path}", self.caddy)
        self.assertNotIn("/mcp/mcp", self.caddy)

    def test_health_is_router_contract_only(self):
        self.assertIn("handle /health", self.caddy)
        self.assertIn("router-ok", self.caddy)
        self.assertIn("does NOT probe downstream", self.caddy)


class ComposeTests(unittest.TestCase):
    def setUp(self):
        self.compose = gen_deploy.render_compose("caddy:2.10.2-alpine")

    def test_services_use_digest_env_failclosed(self):
        for s in contract.SERVICES:
            env = contract.IMAGE_ENV_BY_KEY[s.key]
            self.assertIn(f"${{{env}:?", self.compose)

    def test_no_child_host_ports_only_expose(self):
        # Child services must only "expose:", not publish host "ports:".
        # Only the caddy block has a ports: mapping to 8080.
        self.assertEqual(self.compose.count("ports:"), 1)
        self.assertIn('      - "8080:8080"', self.compose)
        for s in contract.SERVICES:
            self.assertIn(f'      - "{s.mcp_port}"', self.compose)

    def test_no_data_volumes_on_children(self):
        # The only volume mount is the Caddyfile.
        self.assertIn("- ./Caddyfile:/etc/caddy/Caddyfile:ro", self.compose)
        self.assertNotIn(":/data", self.compose)

    def test_caddy_depends_on_all_healthy(self):
        for s in contract.SERVICES:
            self.assertIn(f"      {s.key}:\n        condition: service_healthy", self.compose)

    def test_caddy_image_not_latest(self):
        self.assertIn("image: caddy:2.10.2-alpine", self.compose)
        self.assertNotIn("caddy:latest", self.compose)


class GenDeployCLITests(unittest.TestCase):
    def _lock(self) -> dict:
        images = {}
        digest = "sha256:" + "a" * 64
        for s in contract.SERVICES:
            images[s.key] = {
                "image_leaf": s.image_leaf,
                "tag_ref": f"ghcr.io/o/{s.image_leaf}:r1",
                "digest": digest,
                "pinned_ref": f"ghcr.io/o/{s.image_leaf}@{digest}",
            }
        return {"release": "r1", "owner": "o", "images": images}

    def test_full_render(self):
        with tempfile.TemporaryDirectory() as d:
            lock = Path(d) / "image-lock.json"
            lock.write_text(json.dumps(self._lock()))
            out = Path(d) / "deploy"
            with _quiet():
                rc = gen_deploy.main(["--image-lock", str(lock), "--out-dir", str(out)])
            self.assertEqual(rc, 0)
            env = (out / "images.env").read_text()
            self.assertIn("PM_IMAGE=ghcr.io/o/bench-pm-mcp@sha256:" + "a" * 64, env)
            self.assertTrue((out / "Caddyfile").is_file())
            self.assertTrue((out / "docker-compose.yaml").is_file())

    def test_rejects_latest_caddy(self):
        with tempfile.TemporaryDirectory() as d:
            lock = Path(d) / "image-lock.json"
            lock.write_text(json.dumps(self._lock()))
            with _quiet():
                rc = gen_deploy.main([
                "--image-lock", str(lock), "--out-dir", str(Path(d) / "o"),
                "--caddy-image", "caddy:latest",
            ])
            self.assertEqual(rc, 2)

    def test_rejects_lock_without_digest(self):
        bad = self._lock()
        bad["images"]["pm"]["digest"] = "notadigest"
        with tempfile.TemporaryDirectory() as d:
            lock = Path(d) / "image-lock.json"
            lock.write_text(json.dumps(bad))
            with _quiet():
                rc = gen_deploy.main(["--image-lock", str(lock), "--out-dir", str(Path(d) / "o")])
            self.assertEqual(rc, 2)


# --------------------------------------------------------------------------- #
# build_images validation                                                     #
# --------------------------------------------------------------------------- #
class BuildValidationTests(unittest.TestCase):
    def test_owner_lowercased_and_validated(self):
        self.assertEqual(build_images.validate_owner("MyOrg"), "myorg")
        for bad in ("", "has/slash", "a" * 40, "bad space", "-"):
            with self.assertRaises(build_images.BuildError):
                build_images.validate_owner(bad)

    def test_release_rejects_mutable(self):
        self.assertEqual(build_images.validate_release("v2-20261008-r1"), "v2-20261008-r1")
        self.assertEqual(build_images.validate_release("v2"), "v2")
        for bad in ("latest", "bad tag", "a/b", ""):
            with self.assertRaises(build_images.BuildError):
                build_images.validate_release(bad)

    def test_image_ref_shape(self):
        pm = contract.SERVICE_BY_KEY["pm"]
        ref = build_images.image_ref("acme", pm, "v2-x")
        self.assertEqual(ref, "ghcr.io/acme/bench-pm-mcp:v2-x")

    def test_resolve_digest_dry_in_publish(self):
        # publish --dry-run writes a placeholder lock without touching Docker.
        # Can't easily run cmd_publish without a repo build dir; just confirm the
        # env var mapping is complete.
        self.assertEqual(set(contract.IMAGE_ENV_BY_KEY), {s.key for s in contract.SERVICES})


# --------------------------------------------------------------------------- #
# pins and tracked deploy/                                                    #
# --------------------------------------------------------------------------- #
REPO_ROOT = Path(__file__).resolve().parents[3]


class PinTests(unittest.TestCase):
    def test_pins_come_from_dataset_toml(self):
        for rel in (contract.MCP_SERVERS_ZIP, contract.DATA_ZIP):
            pin = contract.pinned_sha256(REPO_ROOT, rel)
            self.assertRegex(pin, r"^[0-9a-f]{64}$")

    def test_missing_pin_raises(self):
        with self.assertRaises(KeyError):
            contract.pinned_sha256(REPO_ROOT, "artifacts/nope.zip")


class TrackedDeployTests(unittest.TestCase):
    def test_lock_copied_into_out_dir(self):
        with tempfile.TemporaryDirectory() as d:
            lock = Path(d) / "image-lock.json"
            lock.write_text(json.dumps(GenDeployCLITests()._lock()))
            out = Path(d) / "deploy"
            with _quiet():
                self.assertEqual(
                    gen_deploy.main(["--image-lock", str(lock), "--out-dir", str(out)]), 0
                )
            self.assertEqual(json.loads((out / "image-lock.json").read_text())["release"], "r1")

    def test_tracked_deploy_files_regenerate_exactly(self):
        tracked = gen_deploy.DEPLOY_DIR
        with tempfile.TemporaryDirectory() as d:
            with _quiet():
                rc = gen_deploy.main([
                    "--image-lock", str(tracked / "image-lock.json"), "--out-dir", d,
                ])
            self.assertEqual(rc, 0)
            for name in ("Caddyfile", "docker-compose.yaml", "images.env", "image-lock.json"):
                self.assertEqual((Path(d) / name).read_text(), (tracked / name).read_text(), name)


if __name__ == "__main__":
    unittest.main()
