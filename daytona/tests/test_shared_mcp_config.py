import copy
import json
import stat

import pytest
import shared_mcp_config as cfg

TEMPLATE = {
    "mcpServers": {
        name: {
            "type": "http",
            "url": f"http://host.docker.internal:{port}/mcp",
            "description": f"{name} description",
        }
        for name, port in cfg.TEMPLATE_PORTS.items()
    }
}
SIGNED = "https://8080-zqsignedtoken123.daytonaproxy01.net"


def repo_template():
    try:
        return cfg.find_template()
    except cfg.ConfigError:
        pytest.skip("not inside a repo checkout")


def write(tmp_path, data, name="mcp.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return path


def test_repo_template_is_discovered_and_valid():
    path = repo_template()
    assert (path.parent / "dataset.toml").is_file()
    cfg.load_template(path)


def test_explicit_template_wins(tmp_path):
    path = write(tmp_path, TEMPLATE)
    assert cfg.find_template(path) == path.resolve()


def test_missing_explicit_template_fails(tmp_path):
    with pytest.raises(cfg.ConfigError, match="not found"):
        cfg.find_template(tmp_path / "nope.json")


def test_render_one_origin_six_routes_no_headers():
    servers = cfg.render_config(TEMPLATE, SIGNED + "/")["mcpServers"]
    assert list(servers) == list(TEMPLATE["mcpServers"])
    for name, route in cfg.SERVICE_ROUTES.items():
        assert servers[name]["url"] == f"{SIGNED}{route}/mcp"
        assert servers[name]["description"] == f"{name} description"
        assert servers[name]["type"] == "http"
        assert "headers" not in servers[name]


def test_render_does_not_mutate_template():
    before = copy.deepcopy(TEMPLATE)
    cfg.render_config(TEMPLATE, SIGNED)
    assert TEMPLATE == before


def test_root_mcp_json_is_unchanged_by_render():
    path = repo_template()
    before = path.read_bytes()
    cfg.render_config(cfg.load_template(path), SIGNED)
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "url, match",
    [
        ("http://8080-tok.example", "https"),
        ("https://u:p@8080-tok.example", "userinfo"),
        ("https://8080-tok.example/?x=1", "bare origin"),
        ("https://8080-tok.example/pm", "bare origin"),
        ("https://8011-tok.example", "8080-"),
    ],
)
def test_unsafe_router_url_rejected(url, match):
    with pytest.raises(cfg.ConfigError, match=match):
        cfg.render_config(TEMPLATE, url)


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda s: s["crm"].update(url="http://host.docker.internal:9999/mcp"), "8012"),
        (lambda s: s["pm"].update(url="http://host.docker.internal:8011/sse"), "8011/mcp"),
        (lambda s: s.pop("support"), "support"),
        (lambda s: s["email"].update(type="stdio"), "type 'http'"),
    ],
)
def test_bad_template_rejected(tmp_path, mutate, match):
    bad = copy.deepcopy(TEMPLATE)
    mutate(bad["mcpServers"])
    with pytest.raises(cfg.ConfigError, match=match):
        cfg.load_template(write(tmp_path, bad))


def test_validate_rejects_headers_mixed_origins_and_wrong_routes():
    good = cfg.render_config(TEMPLATE, SIGNED)
    for mutate, match in [
        (lambda s: s["pm"].update(headers={"x": "y"}), "headers"),
        (lambda s: s["pm"].update(url="https://8080-other.example/pm/mcp"), "one router"),
        (lambda s: s["pm"].update(url=f"{SIGNED}/crm/mcp"), "/pm/mcp"),
    ]:
        config = copy.deepcopy(good)
        mutate(config["mcpServers"])
        with pytest.raises(cfg.ConfigError, match=match):
            cfg.validate_remote_config(config)


def test_signed_token_extracted_from_host():
    assert cfg.signed_token(f"{SIGNED}/pm/mcp") == "zqsignedtoken123"
    assert cfg.config_token(cfg.render_config(TEMPLATE, SIGNED)) == "zqsignedtoken123"
    with pytest.raises(cfg.ConfigError):
        cfg.signed_token("https://daytonaproxy01.net/pm/mcp")


def test_write_config_is_0600_and_round_trips(tmp_path):
    config = cfg.render_config(TEMPLATE, SIGNED)
    out = tmp_path / "gen" / "mcp.json"
    cfg.write_config(config, out)
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert cfg.load_remote_config(out) == config
    assert not [p for p in out.parent.iterdir() if p.name.startswith(".")]


def test_symlinked_config_rejected(tmp_path):
    real = write(tmp_path, cfg.render_config(TEMPLATE, SIGNED), "real.json")
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(cfg.ConfigError, match="symlink"):
        cfg.load_remote_config(link)
    with pytest.raises(cfg.ConfigError, match="symlink"):
        cfg.write_config({}, link)
