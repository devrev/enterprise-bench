import json
import shutil
import types

import pytest
import shared_mcp_config as cfg
import shared_mcp_daytona as dep

SIGNED_TOKEN = "zqsignedtoken123"


class FakeSandbox:
    def __init__(self, *, manifest=None, state="started", public=False):
        self.id = "sbx-123"
        self.state = state
        self.public = public
        self.manifest = manifest
        self.commands = []
        self.uploads = []
        self.expired = []
        self.signed_expiry = None
        self.process = types.SimpleNamespace(exec=self._exec)
        self.fs = types.SimpleNamespace(upload_file=self._upload)

    def _exec(self, command, cwd=None, env=None, timeout=None):
        self.commands.append((command, env))
        if command.startswith("cat ") and self.manifest:
            return types.SimpleNamespace(exit_code=0, result=json.dumps(self.manifest))
        return types.SimpleNamespace(exit_code=0, result="")

    def _upload(self, src, dst):
        self.uploads.append(dst)

    def create_signed_preview_url(self, port, expires_in_seconds=None):
        self.signed_expiry = expires_in_seconds
        return types.SimpleNamespace(url=f"https://{port}-{SIGNED_TOKEN}.daytonaproxy01.net")

    def expire_signed_preview_url(self, port, token):
        self.expired.append((port, token))


class FakeSnapshots:
    def __init__(self, states=None):
        self.states = dict(states or {})
        self.created = []
        self.activated = []

    def activate(self, name):
        self.activated.append(name)
        self.states[name] = "active"

    def get(self, name):
        if name not in self.states:
            raise RuntimeError("Snapshot not found")
        return types.SimpleNamespace(state=self.states[name])

    def create(self, params, on_logs=None, timeout=None):
        self.created.append(params)
        self.states[params.name] = "active"


def use(monkeypatch, sandbox, snapshots=None):
    client = types.SimpleNamespace(get=lambda name: sandbox, snapshot=snapshots or FakeSnapshots())
    monkeypatch.setattr(dep, "daytona_client", lambda: client)
    return client


REAL_REPO_ROOT = dep.REPO_ROOT


@pytest.fixture(autouse=True)
def no_repo_env(monkeypatch, tmp_path):
    """Isolate from the developer's .env; keep the real dataset.toml and artifacts."""
    shutil.copy(REAL_REPO_ROOT / "dataset.toml", tmp_path / "dataset.toml")
    (tmp_path / "artifacts").symlink_to(REAL_REPO_ROOT / "artifacts")
    monkeypatch.setattr(dep, "REPO_ROOT", tmp_path)


@pytest.fixture
def no_daytona(monkeypatch):
    def forbidden():
        raise AssertionError("Daytona must not be contacted")

    monkeypatch.setattr(dep, "daytona_client", forbidden)


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(dep.time, "sleep", lambda s: None)


def test_help_does_not_contact_daytona(no_daytona, capsys):
    with pytest.raises(SystemExit) as exc:
        dep.main(["--help"])
    assert exc.value.code == 0
    assert "revoke" in capsys.readouterr().out


def test_dry_run_does_not_contact_daytona(no_daytona, tmp_path):
    rc = dep.main(["deploy", "--sandbox-name", "shared-mcp",
                   "--manifest", str(tmp_path / "m.json"), "--dry-run"])
    assert rc == 0
    assert not (tmp_path / "m.json").exists()


def test_tracked_lock_matches_images_env():
    lock = dep.load_lock()
    assert len(lock["images"]) == 6
    assert all("@sha256:" in e["pinned_ref"] for e in lock["images"].values())


def test_mismatched_images_env_rejected(tmp_path):
    shutil.copytree(dep.DEPLOY_DIR, tmp_path / "deploy")
    env = tmp_path / "deploy" / "images.env"
    env.write_text(env.read_text().replace("sha256:", "sha256:0", 1))
    with pytest.raises(dep.DeployError, match="does not match"):
        dep.load_lock(tmp_path / "deploy")


def test_manifest_is_non_secret_and_complete():
    manifest = dep.build_manifest(sandbox_id="sbx", sandbox_name="n", lock=dep.load_lock(),
                                  started_at="t")
    assert manifest["routes"] == cfg.SERVICE_ROUTES
    assert len(manifest["images"]) == 6
    assert {"sandbox_id", "repo", "release", "started_at"} <= set(manifest)
    assert "token" not in json.dumps(manifest).lower()


def test_deploy_uploads_pulls_and_records_manifest(monkeypatch, tmp_path, fast):
    sandbox = FakeSandbox()
    use(monkeypatch, sandbox)
    monkeypatch.delenv("GHCR_TOKEN", raising=False)
    rc = dep.main(["deploy", "--sandbox-name", "shared-mcp",
                   "--manifest", str(tmp_path / "m.json")])
    assert rc == 0
    commands = [c for c, _ in sandbox.commands]
    assert any("pull" in c for c in commands) and any("up -d" in c for c in commands)
    assert not any("docker login" in c for c in commands)
    assert {f"{dep.REMOTE_ROOT}/{n}" for n in dep.DEPLOY_FILES} <= set(sandbox.uploads)
    manifest = json.loads((tmp_path / "m.json").read_text())
    assert manifest["sandbox_id"] == "sbx-123" and manifest["started_at"]


def test_deploy_ghcr_login_keeps_token_out_of_command(monkeypatch, tmp_path, fast):
    sandbox = FakeSandbox()
    use(monkeypatch, sandbox)
    monkeypatch.setenv("GHCR_TOKEN", "ghp_fixture_secret")
    assert dep.main(["deploy", "--sandbox-name", "shared-mcp",
                     "--manifest", str(tmp_path / "m.json")]) == 0
    login = [(c, env) for c, env in sandbox.commands if "docker login" in c]
    assert login and "ghp_fixture_secret" not in login[0][0]
    assert login[0][1]["GHCR_TOKEN"] == "ghp_fixture_secret"


def test_deploy_same_release_keeps_original_start(monkeypatch, tmp_path, fast):
    existing = dep.build_manifest(sandbox_id="sbx-123", sandbox_name="shared-mcp",
                                  lock=dep.load_lock(), started_at="2026-01-01T00:00:00+00:00")
    use(monkeypatch, FakeSandbox(manifest=existing))
    assert dep.main(["deploy", "--sandbox-name", "shared-mcp",
                     "--manifest", str(tmp_path / "m.json")]) == 0
    assert json.loads((tmp_path / "m.json").read_text())["started_at"].startswith("2026-01-01")


def test_env_file_loaded_without_overriding_environment(monkeypatch, tmp_path, fast):
    (tmp_path / ".env").write_text("GHCR_TOKEN=from_env_file\nDAYTONA_X_TEST=1\n")
    monkeypatch.delenv("GHCR_TOKEN", raising=False)
    monkeypatch.setenv("DAYTONA_X_TEST", "from_shell")
    sandbox = FakeSandbox()
    use(monkeypatch, sandbox)
    assert dep.main(["deploy", "--sandbox-name", "shared-mcp",
                     "--manifest", str(tmp_path / "m.json")]) == 0
    login = [env for c, env in sandbox.commands if "docker login" in c]
    assert login and login[0]["GHCR_TOKEN"] == "from_env_file"
    assert dep.os.environ["DAYTONA_X_TEST"] == "from_shell"
    monkeypatch.delenv("GHCR_TOKEN", raising=False)


def test_deploy_refuses_public_sandbox(monkeypatch, tmp_path):
    sandbox = FakeSandbox(public=True)
    use(monkeypatch, sandbox)
    assert dep.main(["deploy", "--sandbox-name", "shared-mcp",
                     "--manifest", str(tmp_path / "m.json")]) == 2
    assert sandbox.uploads == []


def run_config(monkeypatch, tmp_path, sandbox, *extra):
    use(monkeypatch, sandbox)
    out = tmp_path / "mcp.json"
    rc = dep.main(["config", "--sandbox-name", "shared-mcp", "--output", str(out), *extra])
    return rc, out


def test_config_renders_signed_router_urls(monkeypatch, tmp_path):
    sandbox = FakeSandbox(manifest={"images": {}})
    rc, out = run_config(monkeypatch, tmp_path, sandbox, "--expires-in", "3600")
    assert rc == 0 and sandbox.signed_expiry == 3600
    servers = json.loads(out.read_text())["mcpServers"]
    assert servers["support"]["url"] == (
        f"https://8080-{SIGNED_TOKEN}.daytonaproxy01.net/support/mcp"
    )
    assert all("headers" not in s for s in servers.values())


@pytest.mark.parametrize("seconds", ["0", "86401"])
def test_config_rejects_out_of_range_expiry(monkeypatch, tmp_path, seconds):
    rc, out = run_config(monkeypatch, tmp_path, FakeSandbox(manifest={"a": 1}),
                         "--expires-in", seconds)
    assert rc == 2 and not out.exists()


def test_config_fails_when_not_deployed(monkeypatch, tmp_path):
    rc, out = run_config(monkeypatch, tmp_path, FakeSandbox(manifest=None))
    assert rc == 2 and not out.exists()


def test_config_fails_when_stopped(monkeypatch, tmp_path):
    rc, out = run_config(monkeypatch, tmp_path, FakeSandbox(manifest={"a": 1}, state="stopped"))
    assert rc == 2 and not out.exists()


def test_config_writes_job_config_with_task_snapshot(monkeypatch, tmp_path):
    rc, out = run_config(monkeypatch, tmp_path, FakeSandbox(manifest={"a": 1}))
    job = (out.parent / "job.yaml").read_text()
    assert rc == 0
    assert "type: daytona" in job
    assert f"snapshot_template_name: {dep.task_snapshot_name()}" in job


def test_task_snapshot_name_tracks_base_image_digest():
    digest = dep.pinned_digest(dep.BASE_IMAGE_ZIP).split(":", 1)[1]
    assert dep.task_snapshot_name() == f"enterprise-bench-task-env-{digest[:12]}"


def test_snapshot_is_noop_when_active(monkeypatch):
    snapshots = FakeSnapshots({dep.task_snapshot_name(): "active"})
    use(monkeypatch, FakeSandbox(), snapshots)
    assert dep.main(["snapshot"]) == 0
    assert snapshots.created == []


def test_snapshot_builds_from_pinned_base_image(monkeypatch):
    snapshots = FakeSnapshots()
    use(monkeypatch, FakeSandbox(), snapshots)
    assert dep.main(["snapshot"]) == 0
    (params,) = snapshots.created
    assert params.name == dep.task_snapshot_name()
    assert params.resources.cpu == 2 and params.resources.memory == 2


def test_snapshot_reactivates_inactive_snapshot(monkeypatch):
    name = dep.task_snapshot_name()
    snapshots = FakeSnapshots({name: "inactive"})
    use(monkeypatch, FakeSandbox(), snapshots)
    assert dep.main(["snapshot"]) == 0
    assert snapshots.activated == [name]
    assert snapshots.created == []


def test_snapshot_refuses_errored_snapshot(monkeypatch):
    use(monkeypatch, FakeSandbox(), FakeSnapshots({dep.task_snapshot_name(): "error"}))
    assert dep.main(["snapshot"]) == 2


def test_snapshot_print_reports_missing(monkeypatch):
    use(monkeypatch, FakeSandbox(), FakeSnapshots())
    assert dep.main(["snapshot", "--print"]) == 1


def test_tampered_base_image_rejected(monkeypatch, tmp_path):
    toml = tmp_path / "dataset.toml"
    toml.write_text(toml.read_text().replace(
        dep.pinned_digest(dep.BASE_IMAGE_ZIP), "sha256:" + "0" * 64))
    with pytest.raises(dep.DeployError, match="does not match"):
        dep.extract_base_image(tmp_path / "x")


def test_server_snapshot_refuses_existing_without_replace(monkeypatch):
    snapshots = FakeSnapshots({dep.SERVER_SNAPSHOT: "active"})
    use(monkeypatch, FakeSandbox(), snapshots)
    assert dep.main(["server-snapshot"]) == 2
    assert snapshots.created == []


def test_server_snapshot_builds_from_tracked_dockerfile(monkeypatch):
    snapshots = FakeSnapshots()
    use(monkeypatch, FakeSandbox(), snapshots)
    assert dep.main(["server-snapshot"]) == 0
    (params,) = snapshots.created
    assert params.name == "shared-mcp-servers-v2"
    assert params.resources.cpu == 4 and params.resources.disk == 10


def test_server_dockerfile_is_dind_with_deploy_files():
    dockerfile = (dep.DEPLOY_DIR / "Dockerfile").read_text()
    assert "FROM docker:28.3.3-dind" in dockerfile
    for name in (*dep.DEPLOY_FILES, "image-lock.json"):
        assert name in dockerfile


def test_deploy_starts_dockerd_before_compose(monkeypatch, tmp_path, fast):
    sandbox = FakeSandbox()
    use(monkeypatch, sandbox)
    assert dep.main(["deploy", "--sandbox-name", "shared-mcp",
                     "--manifest", str(tmp_path / "m.json")]) == 0
    commands = [c for c, _ in sandbox.commands]
    first_dockerd = next(i for i, c in enumerate(commands) if "dockerd" in c)
    first_compose = next(i for i, c in enumerate(commands) if "up -d" in c)
    assert first_dockerd < first_compose


def test_revoke_expires_the_config_token(monkeypatch, tmp_path):
    sandbox = FakeSandbox(manifest={"a": 1})
    _, out = run_config(monkeypatch, tmp_path, sandbox)
    assert dep.main(["revoke", "--sandbox-name", "shared-mcp", "--config", str(out)]) == 0
    assert sandbox.expired == [(8080, SIGNED_TOKEN)]


def test_stop_requires_yes(no_daytona):
    assert dep.main(["stop", "--sandbox-name", "shared-mcp"]) == 2


def test_probe_script_covers_every_route():
    script = dep.probe_script()
    for route in cfg.SERVICE_ROUTES.values():
        assert route in script
    assert ":8080" in script and "serverInfo" in script


def test_invalid_sandbox_name_rejected(no_daytona):
    with pytest.raises(SystemExit):
        dep.main(["inspect", "--sandbox-name", "bad name"])
