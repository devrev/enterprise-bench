"""Tests for the offline leaderboard validator (scripts/validate_leaderboard.py)."""

from __future__ import annotations

import uuid
from pathlib import Path

import validate_leaderboard as vl
import yaml


def write_entry(
    directory: Path,
    name: str,
    *,
    job_id: str | None = None,
    trial_ids: list[str] | None = None,
    metrics: dict | None = None,
    metadata: dict | None = None,
) -> Path:
    """Write a schema-conformant entry; fields can be overridden per test."""
    job_id = job_id or str(uuid.uuid4())
    trial_ids = trial_ids or [str(uuid.uuid4()), str(uuid.uuid4())]
    metrics = {
        "accuracy": 50.0,
        "display_accuracy": "50.0%",
        "uncached_input_tokens": 10,
        "cached_input_tokens": 20,
        "output_tokens": 5,
        "total_tokens": 40,
        "avg_trial_duration_sec": 1.0,
        "pass_at_2": 0.5,
        "pass_at_3": 0.5,
        "pass_at_4": 0.5,
        "pass_at_5": 0.5,
        "n_trials": len(trial_ids),
        "total_cost_usd": 1.0,
        "display_total_cost_usd": "$1.00",
        **(metrics or {}),
    }
    row = {
        "metadata": {
            "agent_display_name": "Test Agent",
            "model_display_name": "Test Model",
            "agent_org_display_name": "Test Org",
            "model_org_display_name": "Test Org",
            **(metadata or {}),
        },
        "metrics": metrics,
        "status": "display",
        "trial_ids": trial_ids,
    }
    path = directory / name
    path.write_text(
        f"# Test / Entry\n"
        f"# Source job: {job_id}\n"
        f"# https://hub.harborframework.com/jobs/{job_id}\n"
        + yaml.safe_dump({"rows": [row]}, sort_keys=False)
    )
    return path


def test_repository_entries_pass() -> None:
    """The committed entries keep validating with default settings."""
    assert vl.main([]) == 0


def test_entries_dir_override(tmp_path: Path) -> None:
    """--entries-dir validates another directory (CI: the PR checkout's data)."""
    write_entry(tmp_path, "2026-01-01__agent__model.yaml")
    assert vl.main(["--entries-dir", str(tmp_path)]) == 0


def test_missing_entries_dir_fails(tmp_path: Path) -> None:
    missing = tmp_path / "nowhere"
    assert vl.main(["--entries-dir", str(missing)]) == 1


def test_cross_file_duplicate_trial_detected(tmp_path: Path) -> None:
    """The same trial id may not be claimed by two entries."""
    shared = str(uuid.uuid4())
    write_entry(tmp_path, "2026-01-01__agent__model.yaml", trial_ids=[shared])
    write_entry(tmp_path, "2026-01-02__other__model.yaml", trial_ids=[shared])
    assert vl.main(["--entries-dir", str(tmp_path)]) == 1


def test_placeholder_metadata_fails(tmp_path: Path) -> None:
    write_entry(
        tmp_path,
        "2026-01-01__agent__model.yaml",
        metadata={"agent_display_name": "<Agent name>"},
    )
    assert vl.main(["--entries-dir", str(tmp_path)]) == 1


def test_template_file_excluded(tmp_path: Path) -> None:
    path = write_entry(tmp_path, vl.TEMPLATE_PATH.name)
    # row-template.yaml contains placeholders and would fail if validated.
    path.write_text("rows: []\n")
    assert vl.main(["--entries-dir", str(tmp_path)]) == 0
