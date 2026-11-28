"""Tests for the provenance stage (scripts/validate_provenance.py).

All Hub responses are mocked; no test touches the network.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
import validate_provenance as vp
import yaml

# --- Hub page fixtures ------------------------------------------------------

JOB_ID = "f2c616ff-fc60-4eec-be74-4ec63b918d9e"

# Values mirrored from a real public Hub job page (see issue #168 fixtures).
JOB_RECORD = {
    "id": JOB_ID,
    "visibility": "public",
    "n_total_trials": 210,
    "n_completed_trials": 210,
    "n_planned_trials": 210,
    "cost_usd": 58.7366381,
    "input_tokens": 201422160,
    "output_tokens": 1236272,
    "cache_tokens": 187616001,
    "stats": {"avg_reward": 0.657142857142857, "n_trials": 210},
    "config": {
        "n_attempts": 5,
        "verifier": {"env": {"JUDGE_MODEL": "openai/gpt-6-luna"}},
        "datasets": [{"path": "Enterprise-Bench/l1-l2-bench-2.0"}],
    },
}

MATCHING_METRICS = {
    "accuracy": 65.71,
    "display_accuracy": "65.7%",
    "uncached_input_tokens": 13806159,
    "cached_input_tokens": 187616001,
    "output_tokens": 1236272,
    "total_tokens": 202658432,
    "avg_trial_duration_sec": 260.95,
    "pass_at_2": 0.7381,
    "pass_at_3": 0.7738,
    "pass_at_4": 0.7952,
    "pass_at_5": 0.8095,
    "n_trials": 210,
    "total_cost_usd": 58.74,
    "display_total_cost_usd": "$58.74",
}


def make_job_page(job: dict | None = None, trial_ids: list[str] | None = None) -> str:
    """Render a synthetic Hub job page: flight chunk embedding the job record."""
    # Compact separators: the real flight data is not pretty-printed.
    text = json.dumps({"job": JOB_RECORD if job is None else job},
                      separators=(",", ":"))
    for trial_id in trial_ids or []:
        text += '{"id":"%s","config_values":{}}' % trial_id
    chunk = json.dumps("2:" + text)
    return f"<html><body><script>self.__next_f.push([1,{chunk}])</script></body></html>"


def write_entry(
    directory: Path,
    name: str,
    *,
    job_id: str = JOB_ID,
    metrics: dict | None = None,
    trial_ids: list[str] | None = None,
) -> Path:
    trial_ids = trial_ids or [str(uuid.uuid4()) for _ in range(3)]
    row = {
        "metadata": {
            "agent_display_name": "Agent",
            "model_display_name": "Model",
            "agent_org_display_name": "Org",
            "model_org_display_name": "Org",
        },
        "metrics": {**MATCHING_METRICS, **(metrics or {})},
        "status": "display",
        "trial_ids": trial_ids,
    }
    path = directory / name
    path.write_text(
        f"# Agent / Model\n"
        f"# Source job: {job_id}\n"
        f"# https://hub.harborframework.com/jobs/{job_id}\n"
        + yaml.safe_dump({"rows": [row]}, sort_keys=False)
    )
    return path


def fetcherReturning(status: int, body: str):
    return lambda url: (status, body)


def make_multi_chunk_page(
    job: dict | None = None, trial_ids: list[str] | None = None
) -> str:
    """Same page, but the flight text is split across two push() chunks.

    Regression guard for the chunk-joining logic the real page relies on.
    """
    text = json.dumps({"job": JOB_RECORD if job is None else job},
                      separators=(",", ":"))
    for trial_id in trial_ids or []:
        text += '{"id":"%s","config_values":{}}' % trial_id
    half = len(text) // 2
    chunks = ", ".join(json.dumps(part) for part in (text[:half], text[half:]))
    return (
        "<html><body>"
        f"<script>self.__next_f.push([1,{chunks.split(', ')[0]}])</script>"
        f"<script>self.__next_f.push([1,{chunks.split(', ')[1]}])</script>"
        "</body></html>"
    )


def full_cohort_trial_ids(n: int = 210) -> list[str]:
    return [str(uuid.UUID(int=i)) for i in range(1, n + 1)]


# --- verify_entry -----------------------------------------------------------

def config() -> dict:
    errors: list[str] = []
    conf = vp.load_provenance_config(errors)
    assert not errors
    return conf


def test_matching_public_job_passes(tmp_path: Path) -> None:
    """An entry whose ids all appear on the page verifies cleanly."""
    ids = [str(uuid.uuid4()) for _ in range(3)]
    entry = write_entry(tmp_path, "2026-01-01__agent__model.yaml", trial_ids=ids)
    result = vp.verify_entry(
        entry.name, entry.read_text(), yaml.safe_load(entry.read_text()),
        True, config(), fetcherReturning(200, make_job_page(trial_ids=ids)),
    )
    assert result["status"] == "pass"
    assert result["findings"] == []


def test_glm_accuracy_reconciles_via_pass_counts(tmp_path: Path) -> None:
    """The GLM row (issue #168/#147): entry accuracy 50/210 = 23.81 while the
    job's avg reward is 50/209 (one unscored trial drops out of the Hub mean,
    invisibly). Comparing implied pass counts must reconcile them, not flag
    a correct entry."""
    ids = [str(uuid.uuid4()) for _ in range(3)]
    entry = write_entry(
        tmp_path,
        "2026-01-01__glm__row.yaml",
        metrics={
            **MATCHING_METRICS,
            "accuracy": 23.81,
            "display_accuracy": "23.8%",
            "n_trials": 210,
        },
        trial_ids=ids,
    )
    job = dict(
        JOB_RECORD,
        stats={"avg_reward": 50 / 209, "n_trials": 210},
        n_total_trials=210,
    )
    result = vp.verify_entry(
        entry.name, entry.read_text(), yaml.safe_load(entry.read_text()),
        True, config(), fetcherReturning(200, make_job_page(job, trial_ids=ids)),
    )
    assert result["status"] == "pass"
    assert not any(f["check"] == "accuracy" for f in result["findings"])


def test_accuracy_beyond_pass_tolerance_fails(tmp_path: Path) -> None:
    """A claim that moves the implied pass count beyond the tolerance fails."""
    ids = [str(uuid.uuid4()) for _ in range(3)]
    entry = write_entry(
        tmp_path,
        "2026-01-01__agent__model.yaml",
        metrics={**MATCHING_METRICS, "accuracy": 26.0},
        trial_ids=ids,
    )
    result = vp.verify_entry(
        entry.name, entry.read_text(), yaml.safe_load(entry.read_text()),
        True, config(), fetcherReturning(200, make_job_page(trial_ids=ids)),
    )
    assert result["status"] == "fail"
    finding = next(f for f in result["findings"] if f["check"] == "accuracy")
    assert finding["level"] == "error"


def test_fabricated_entry_fails_when_changed(tmp_path: Path) -> None:
    """The audit's fabricated row (issue #168) must fail this stage."""
    entry = write_entry(
        tmp_path,
        "2026-11-23__fabricated__overstated.yaml",
        metrics={
            "accuracy": 99.91,
            "display_accuracy": "99.9%",
            "uncached_input_tokens": 1000000,
            "cached_input_tokens": 2000000,
            "output_tokens": 500000,
            "total_tokens": 4000000,
            "avg_trial_duration_sec": 12.5,
            "pass_at_2": 0.998,
            "pass_at_3": 0.999,
            "pass_at_4": 1.0,
            "pass_at_5": 1.0,
            "n_trials": 2,
            "total_cost_usd": 0.42,
            "display_total_cost_usd": "$0.42",
        },
        trial_ids=[
            "3f2b8a64-1d47-4c8e-9a51-6b0f2e7d4c93",
            "8e14c7b2-5a39-4f06-bd82-91d3c4a7e5f0",
        ],
    )
    result = vp.verify_entry(
        entry.name, entry.read_text(), yaml.safe_load(entry.read_text()),
        True, config(), fetcherReturning(200, make_job_page()),
    )
    assert result["status"] == "fail"
    checks = {finding["check"] for finding in result["findings"]}
    assert {"n_trials", "accuracy", "tokens", "cost"} <= checks


def test_fabricated_legacy_entry_only_warns(tmp_path: Path) -> None:
    """Unchanged/legacy rows degrade to warnings (migration rule)."""
    entry = write_entry(
        tmp_path,
        "2026-01-01__agent__model.yaml",
        metrics={"accuracy": 99.91, "n_trials": 2},
    )
    result = vp.verify_entry(
        entry.name, entry.read_text(), yaml.safe_load(entry.read_text()),
        False, config(), fetcherReturning(200, make_job_page()),
    )
    assert result["status"] == "warn"
    assert all(finding["level"] == "warn" for finding in result["findings"])


def test_missing_job_page_fails_when_changed(tmp_path: Path) -> None:
    entry = write_entry(tmp_path, "2026-01-01__agent__model.yaml")
    result = vp.verify_entry(
        entry.name, entry.read_text(), yaml.safe_load(entry.read_text()),
        True, config(), fetcherReturning(404, ""),
    )
    assert result["status"] == "fail"
    assert result["findings"][0]["check"] == "visibility"


def test_missing_job_page_warns_for_legacy(tmp_path: Path) -> None:
    entry = write_entry(tmp_path, "2026-01-01__agent__model.yaml")
    result = vp.verify_entry(
        entry.name, entry.read_text(), yaml.safe_load(entry.read_text()),
        False, config(), fetcherReturning(404, ""),
    )
    assert result["status"] == "warn"


def test_private_job_fails(tmp_path: Path) -> None:
    entry = write_entry(tmp_path, "2026-01-01__agent__model.yaml")
    job = dict(JOB_RECORD, visibility="private")
    result = vp.verify_entry(
        entry.name, entry.read_text(), yaml.safe_load(entry.read_text()),
        True, config(), fetcherReturning(200, make_job_page(job)),
    )
    assert result["status"] == "fail"
    assert any(f["check"] == "visibility" for f in result["findings"])


def test_network_error_is_inconclusive_not_fatal(tmp_path: Path) -> None:
    """Hub downtime must never produce a red X (issue #168 watch-out)."""
    entry = write_entry(tmp_path, "2026-01-01__agent__model.yaml")

    def broken(url: str) -> tuple[int, str]:
        raise vp.FetchNetworkError("connection reset")

    result = vp.verify_entry(
        entry.name, entry.read_text(), yaml.safe_load(entry.read_text()),
        True, config(), broken,
    )
    assert result["status"] == "warn"
    assert any("inconclusive" in f["message"] for f in result["findings"])


def test_unparseable_page_is_inconclusive(tmp_path: Path) -> None:
    entry = write_entry(tmp_path, "2026-01-01__agent__model.yaml")
    result = vp.verify_entry(
        entry.name, entry.read_text(), yaml.safe_load(entry.read_text()),
        True, config(), fetcherReturning(200, "<html>no flight data</html>"),
    )
    assert result["status"] == "warn"
    assert any(f["check"] == "parse" for f in result["findings"])


def test_wrong_judge_and_dataset_fail(tmp_path: Path) -> None:
    entry = write_entry(tmp_path, "2026-01-01__agent__model.yaml")
    job = dict(JOB_RECORD)
    job["config"] = dict(
        JOB_RECORD["config"],
        verifier={"env": {"JUDGE_MODEL": "openai/gpt-5-mini"}},
        datasets=[{"path": "some-other-bench/v1"}],
    )
    result = vp.verify_entry(
        entry.name, entry.read_text(), yaml.safe_load(entry.read_text()),
        True, config(), fetcherReturning(200, make_job_page(job)),
    )
    assert result["status"] == "fail"
    checks = {finding["check"] for finding in result["findings"]}
    assert {"judge", "dataset"} <= checks


def test_missing_cohort_metadata_is_inconclusive(tmp_path: Path) -> None:
    entry = write_entry(tmp_path, "2026-01-01__agent__model.yaml")
    job = dict(JOB_RECORD)
    job["config"] = {}
    result = vp.verify_entry(
        entry.name, entry.read_text(), yaml.safe_load(entry.read_text()),
        True, config(), fetcherReturning(200, make_job_page(job)),
    )
    assert result["status"] == "warn"
    checks = {finding["check"] for finding in result["findings"]}
    assert {"judge", "dataset", "cohort"} <= checks


def test_wrong_attempt_count_fails(tmp_path: Path) -> None:
    entry = write_entry(tmp_path, "2026-01-01__agent__model.yaml")
    job = dict(JOB_RECORD, config=dict(JOB_RECORD["config"], n_attempts=3))
    result = vp.verify_entry(
        entry.name, entry.read_text(), yaml.safe_load(entry.read_text()),
        True, config(), fetcherReturning(200, make_job_page(job)),
    )
    assert result["status"] == "fail"
    assert any(f["check"] == "cohort" for f in result["findings"])


def test_trial_membership_is_positive_only(tmp_path: Path) -> None:
    """Found ids are confirmed; absent ids are inconclusive, never failures."""
    visible = "f468c271-0450-4d00-b71e-a59af64f5b33"
    entry = write_entry(
        tmp_path,
        "2026-01-01__agent__model.yaml",
        trial_ids=[visible, str(uuid.uuid4()), str(uuid.uuid4())],
    )
    page = make_job_page(trial_ids=[visible])
    result = vp.verify_entry(
        entry.name, entry.read_text(), yaml.safe_load(entry.read_text()),
        True, config(), fetcherReturning(200, page),
    )
    assert result["status"] == "warn"  # inconclusive notes only
    note = next(f for f in result["findings"] if f["check"] == "trial_ownership")
    assert "1 of 3" in note["message"]


def test_zero_confirmed_full_cohort_fails(tmp_path: Path) -> None:
    """Copy-real-metrics + arbitrary uuids must not pass strict mode."""
    entry = write_entry(
        tmp_path,
        "2026-01-01__agent__model.yaml",
        metrics={"n_trials": 210},
        trial_ids=full_cohort_trial_ids(),
    )
    decoys = [str(uuid.UUID(int=i)) for i in range(1000, 1100)]
    page = make_job_page(trial_ids=decoys)
    result = vp.verify_entry(
        entry.name, entry.read_text(), yaml.safe_load(entry.read_text()),
        True, config(), fetcherReturning(200, page),
    )
    assert result["status"] == "fail"
    finding = next(f for f in result["findings"] if f["check"] == "trial_ownership")
    assert finding["level"] == "error"


def test_partially_confirmed_full_cohort_stays_inconclusive(tmp_path: Path) -> None:
    """Some confirmed ids keep the full-cohort claim inconclusive, not failed."""
    decoys = [str(uuid.UUID(int=i)) for i in range(1000, 1100)]
    claimed = decoys[:50] + [str(uuid.UUID(int=i)) for i in range(2000, 2160)]
    entry = write_entry(
        tmp_path,
        "2026-01-01__agent__model.yaml",
        metrics={"n_trials": 210},
        trial_ids=claimed,
    )
    page = make_job_page(trial_ids=decoys)
    result = vp.verify_entry(
        entry.name, entry.read_text(), yaml.safe_load(entry.read_text()),
        True, config(), fetcherReturning(200, page),
    )
    assert result["status"] != "fail"
    note = next(f for f in result["findings"] if f["check"] == "trial_ownership")
    assert "50 of 210" in note["message"]


def test_missing_required_evidence_is_inconclusive(tmp_path: Path) -> None:
    """A record without the required fields must not produce a strict PASS."""
    entry = write_entry(
        tmp_path,
        "2026-01-01__agent__model.yaml",
        metrics={"accuracy": 99.91, "total_tokens": 12345, "total_cost_usd": 9.99},
    )
    job = dict(JOB_RECORD)
    for field in ("n_total_trials", "input_tokens", "output_tokens",
                  "cache_tokens", "cost_usd", "stats"):
        job.pop(field, None)
    result = vp.verify_entry(
        entry.name, entry.read_text(), yaml.safe_load(entry.read_text()),
        True, config(), fetcherReturning(200, make_job_page(job)),
    )
    assert result["status"] != "pass"
    assert any(f["check"] == "evidence" for f in result["findings"])


def test_job_identity_mismatch_fails(tmp_path: Path) -> None:
    """The record must belong to the requested job."""
    entry = write_entry(tmp_path, "2026-01-01__agent__model.yaml")
    job = dict(JOB_RECORD, id=str(uuid.UUID(int=999)))
    result = vp.verify_entry(
        entry.name, entry.read_text(), yaml.safe_load(entry.read_text()),
        True, config(), fetcherReturning(200, make_job_page(job)),
    )
    assert result["status"] == "fail"
    assert any(f["check"] == "identity" for f in result["findings"])


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_service_responses_are_inconclusive(
    tmp_path: Path, status: int
) -> None:
    """Hub downtime / rate limiting must never red-X a changed entry."""
    entry = write_entry(tmp_path, "2026-01-01__agent__model.yaml")
    result = vp.verify_entry(
        entry.name, entry.read_text(), yaml.safe_load(entry.read_text()),
        True, config(), fetcherReturning(status, ""),
    )
    assert result["status"] == "warn"
    assert any(f["check"] == "fetch" for f in result["findings"])


def test_multi_chunk_page_extracts(tmp_path: Path) -> None:
    """The real page splits flight data across push() chunks."""
    ids = [str(uuid.uuid4()) for _ in range(3)]
    entry = write_entry(tmp_path, "2026-01-01__agent__model.yaml", trial_ids=ids)
    result = vp.verify_entry(
        entry.name, entry.read_text(), yaml.safe_load(entry.read_text()),
        True, config(),
        fetcherReturning(200, make_multi_chunk_page(trial_ids=ids)),
    )
    assert result["status"] == "pass"


def _entry_with_rows(tmp_path: Path, name: str, rows: list) -> Path:
    """Write an entry whose 'rows' list is arbitrary (for row-shape tests)."""
    path = tmp_path / name
    path.write_text(
        f"# Test / Rows\n"
        f"# Source job: {JOB_ID}\n"
        f"# https://hub.harborframework.com/jobs/{JOB_ID}\n"
        + yaml.safe_dump({"rows": rows}, sort_keys=False)
    )
    return path


def test_non_mapping_row_warns(tmp_path: Path) -> None:
    """A non-mapping row must not count toward a confident pass."""
    path = _entry_with_rows(
        tmp_path, "2026-01-01__bad__row.yaml", ["oops", {}]
    )
    result = vp.verify_entry(
        path.name, path.read_text(), yaml.safe_load(path.read_text()),
        True, config(), fetcherReturning(200, make_job_page()),
    )
    assert result["status"] == "warn"
    assert any(
        f["check"] == "row_shape" and "row 0" in f["message"]
        for f in result["findings"]
    )


def test_string_metrics_and_trial_ids_warn(tmp_path: Path) -> None:
    """Wrong-typed row fields must be visible in the report."""
    bad_row = {"metadata": {}, "metrics": "unexpected-shape", "trial_ids": "abc"}
    path = _entry_with_rows(tmp_path, "2026-01-01__bad__row.yaml", [bad_row])
    result = vp.verify_entry(
        path.name, path.read_text(), yaml.safe_load(path.read_text()),
        True, config(), fetcherReturning(200, make_job_page()),
    )
    assert result["status"] == "warn"
    row_shape = next(f for f in result["findings"] if f["check"] == "row_shape")
    assert "metrics is not a mapping" in row_shape["message"]
    assert "trial_ids is not a list" in row_shape["message"]


def test_row_missing_metrics_and_trial_ids_warns(tmp_path: Path) -> None:
    """A metadata-only row must not receive a provenance pass."""
    path = _entry_with_rows(
        tmp_path, "2026-01-01__bad__row.yaml",
        [{"metadata": {"agent_display_name": "X"}}],
    )
    result = vp.verify_entry(
        path.name, path.read_text(), yaml.safe_load(path.read_text()),
        True, config(), fetcherReturning(200, make_job_page()),
    )
    assert result["status"] != "pass"
    row_shape = next(f for f in result["findings"] if f["check"] == "row_shape")
    assert "metrics is missing or empty" in row_shape["message"]
    assert "trial_ids is missing or empty" in row_shape["message"]


@pytest.mark.parametrize(
    "document", [{"rows": "oops"}, {"rows": []}, "just a string", {}]
)
def test_malformed_document_warns(tmp_path: Path, document: object) -> None:
    """A malformed entry document must not crash or pass silently."""
    path = _entry_with_rows(tmp_path, "2026-01-01__bad__doc.yaml", [])
    path.write_text(
        f"# Test / Doc\n"
        f"# Source job: {JOB_ID}\n"
        f"# https://hub.harborframework.com/jobs/{JOB_ID}\n"
        + yaml.safe_dump(document)
    )
    result = vp.verify_entry(
        path.name, path.read_text(), yaml.safe_load(path.read_text()),
        True, config(), fetcherReturning(200, make_job_page()),
    )
    assert result["status"] == "warn"
    assert any(f["check"] == "row_shape" for f in result["findings"])


def test_missing_trial_listing_is_inconclusive(tmp_path: Path) -> None:
    """Valid metrics without an embedded listing must not silently pass."""
    entry = write_entry(
        tmp_path,
        "2026-01-01__agent__model.yaml",
        trial_ids=full_cohort_trial_ids(),
    )
    result = vp.verify_entry(
        entry.name, entry.read_text(), yaml.safe_load(entry.read_text()),
        True, config(), fetcherReturning(200, make_job_page()),
    )
    assert result["status"] != "pass"
    note = next(f for f in result["findings"] if f["check"] == "trial_ownership")
    assert "could not be checked" in note["message"]


@pytest.mark.parametrize("bad", ["not-a-number", True, None])
def test_non_numeric_evidence_is_inconclusive(
    tmp_path: Path, bad: object
) -> None:
    """Presence is not enough: required evidence must be numeric."""
    entry = write_entry(
        tmp_path,
        "2026-01-01__agent__model.yaml",
        metrics={"accuracy": 99.91, "total_tokens": 12345, "total_cost_usd": 9.99},
    )
    job = dict(JOB_RECORD, input_tokens=bad)
    result = vp.verify_entry(
        entry.name, entry.read_text(), yaml.safe_load(entry.read_text()),
        True, config(), fetcherReturning(200, make_job_page(job)),
    )
    assert result["status"] != "pass"
    assert any(f["check"] == "evidence" for f in result["findings"])


def test_cache_tokens_missing_is_inconclusive(tmp_path: Path) -> None:
    """The cached/uncached split is part of the token guarantee, so a record
    without cache_tokens must not pass strictly."""
    ids = [str(uuid.uuid4()) for _ in range(3)]
    entry = write_entry(tmp_path, "2026-01-01__agent__model.yaml", trial_ids=ids)
    job = dict(JOB_RECORD)
    job.pop("cache_tokens")
    result = vp.verify_entry(
        entry.name, entry.read_text(), yaml.safe_load(entry.read_text()),
        True, config(), fetcherReturning(200, make_job_page(job, trial_ids=ids)),
    )
    assert result["status"] != "pass"
    assert any(f["check"] == "evidence" for f in result["findings"])


def test_uppercase_job_uuid_and_trial_ids_pass(tmp_path: Path) -> None:
    """UUID comparisons are case-insensitive, like the offline validator's."""
    ids = [uuid.uuid4() for _ in range(3)]
    entry = write_entry(
        tmp_path,
        "2026-01-01__agent__model.yaml",
        job_id=str(JOB_ID).upper(),
        trial_ids=[str(uid).upper() for uid in ids],
    )
    result = vp.verify_entry(
        entry.name, entry.read_text(), yaml.safe_load(entry.read_text()),
        True, config(),
        fetcherReturning(200, make_job_page(trial_ids=[str(uid) for uid in ids])),
    )
    assert result["status"] == "pass"
    assert result["findings"] == []


def test_uppercase_full_cohort_zero_confirmed_fails(tmp_path: Path) -> None:
    """Case-normalized ids must not defeat the zero-overlap rule."""
    claimed = [str(uuid.UUID(int=i)).upper() for i in range(1, 211)]
    entry = write_entry(
        tmp_path,
        "2026-01-01__agent__model.yaml",
        metrics={"n_trials": 210},
        trial_ids=claimed,
    )
    decoys = [str(uuid.UUID(int=i)) for i in range(1000, 1100)]
    page = make_job_page(trial_ids=decoys)
    result = vp.verify_entry(
        entry.name, entry.read_text(), yaml.safe_load(entry.read_text()),
        True, config(), fetcherReturning(200, page),
    )
    assert result["status"] == "fail"
    finding = next(f for f in result["findings"] if f["check"] == "trial_ownership")
    assert finding["level"] == "error"


@pytest.mark.parametrize(
    "field, value",
    [("stats", "unexpected-shape"), ("config", "unexpected-shape")],
)
def test_malformed_nested_fields_are_inconclusive(
    tmp_path: Path, field: str, value: object
) -> None:
    """A nested field of the wrong shape must warn, never crash (AttributeError)."""
    ids = [str(uuid.uuid4()) for _ in range(3)]
    entry = write_entry(tmp_path, "2026-01-01__agent__model.yaml", trial_ids=ids)
    job = dict(JOB_RECORD, **{field: value})
    result = vp.verify_entry(
        entry.name, entry.read_text(), yaml.safe_load(entry.read_text()),
        True, config(), fetcherReturning(200, make_job_page(job, trial_ids=ids)),
    )
    assert result["status"] == "warn"
    if field == "stats":
        assert any(f["check"] == "evidence" for f in result["findings"])
    else:
        assert any(f["check"] in {"judge", "dataset", "cohort"}
                   for f in result["findings"])


def test_workflow_shaped_changed_file_marks_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """git diff writes repository-relative paths into the NUL file — the exact
    shape the workflow produces. Strict mode must still apply to them."""
    fabricated = write_entry(
        tmp_path,
        "2026-11-23__fabricated__overstated.yaml",
        metrics={"accuracy": 99.91, "n_trials": 2},
        trial_ids=full_cohort_trial_ids(2),
    )
    ids = [str(uuid.uuid4()) for _ in range(3)]
    good = write_entry(tmp_path, "2026-01-02__good__row.yaml", trial_ids=ids)
    nul_file = tmp_path / "changed.bin"
    nul_file.write_bytes(
        "\x00".join(
            [f"leaderboard/entries/{fabricated.name}", f"leaderboard/entries/{good.name}"]
        ).encode() + b"\x00"
    )
    page = make_job_page(trial_ids=ids)
    monkeypatch.setattr(vp, "fetch_job_page", lambda url, timeout=20: (200, page))
    assert vp.main(
        ["--entries-dir", str(tmp_path), "--changed-from", str(nul_file), "--json"]
    ) == 1
    report = json.loads(capsys.readouterr().out)
    by_name = {e["entry"]: e for e in report["entries"]}
    assert by_name[fabricated.name]["changed"] is True
    assert by_name[fabricated.name]["status"] == "fail"
    assert by_name[good.name]["changed"] is True
    assert by_name[good.name]["status"] == "pass"


def test_main_changed_from_nul_file_survives_hostile_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Changed-entry names are read from a NUL file, not interpolated."""
    hostile = "2026-01-01__agent name,;$(boom)__model.yaml"
    write_entry(
        tmp_path, hostile,
        metrics={"accuracy": 99.91, "n_trials": 2},
        trial_ids=full_cohort_trial_ids(2),
    )
    benign = write_entry(tmp_path, "2026-01-02__other__row.yaml")
    nul_file = tmp_path / "changed.bin"
    nul_file.write_bytes("\x00".join([hostile, benign.name]).encode() + b"\x00")
    page = make_job_page()
    monkeypatch.setattr(vp, "fetch_job_page", lambda url, timeout=20: (200, page))
    # Exit 1 proves the hostile name was matched as changed (strict) — if the
    # name had failed to match, it would have downgraded to legacy warn.
    assert vp.main(["--entries-dir", str(tmp_path), "--changed-from", str(nul_file)]) == 1


def test_main_deleted_changed_entry_is_not_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PR that deletes an entry has nothing to verify for it."""
    nul_file = tmp_path / "changed.bin"
    nul_file.write_bytes(b"2026-01-01__gone__row.yaml\x00")
    write_entry(tmp_path, "2026-01-02__other__row.yaml")
    page = make_job_page()
    monkeypatch.setattr(vp, "fetch_job_page", lambda url, timeout=20: (200, page))
    assert vp.main(
        ["--entries-dir", str(tmp_path), "--changed-from", str(nul_file)]
    ) == 0


def test_main_passes_timeout_to_fetcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    captured: dict[str, float] = {}

    def spy(url: str, timeout: float = 20) -> tuple[int, str]:
        captured["timeout"] = timeout
        return 404, ""

    monkeypatch.setattr(vp, "fetch_job_page", spy)
    write_entry(tmp_path, "2026-01-01__agent__model.yaml")
    vp.main(["--entries-dir", str(tmp_path), "--timeout", "5"])
    assert captured["timeout"] == 5.0


# --- config -----------------------------------------------------------------

def test_config_defaults_from_leaderboard_yaml() -> None:
    conf = config()
    assert conf["expected"]["dataset_stem"] == "l1-l2-bench"
    assert conf["expected"]["judge"] == "gpt-6-luna"
    assert conf["expected"]["cohort"]["trials"] == 210


# --- main / CLI -------------------------------------------------------------

def test_main_fails_on_fabricated_changed_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    write_entry(
        tmp_path,
        "2026-11-23__fabricated__overstated.yaml",
        metrics={"accuracy": 99.91, "n_trials": 2},
    )
    page = make_job_page()
    monkeypatch.setattr(vp, "fetch_job_page", lambda url, timeout=20: (200, page))
    assert vp.main(["--entries-dir", str(tmp_path)]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_main_legacy_mode_never_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_entry(
        tmp_path, "2026-01-01__agent__model.yaml",
        metrics={"accuracy": 99.91, "n_trials": 2},
    )
    page = make_job_page()
    monkeypatch.setattr(vp, "fetch_job_page", lambda url, timeout=20: (200, page))
    assert vp.main(["--entries-dir", str(tmp_path), "--legacy"]) == 0


def test_main_json_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    ids = [str(uuid.uuid4()) for _ in range(3)]
    write_entry(tmp_path, "2026-01-01__agent__model.yaml", trial_ids=ids)
    page = make_job_page(trial_ids=ids)
    monkeypatch.setattr(vp, "fetch_job_page", lambda url, timeout=20: (200, page))
    assert vp.main(["--entries-dir", str(tmp_path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["summary"]["passed"] == 1
    assert report["entries"][0]["job_id"] == JOB_ID


def test_main_changed_subset_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only --changed entries get strict treatment; others warn."""
    write_entry(
        tmp_path, "2026-01-01__old__row.yaml",
        metrics={"accuracy": 99.91, "n_trials": 2},
    )
    write_entry(
        tmp_path, "2026-01-02__new__row.yaml",
        metrics={"accuracy": 99.91, "n_trials": 2},
    )
    page = make_job_page()
    monkeypatch.setattr(vp, "fetch_job_page", lambda url, timeout=20: (200, page))
    assert vp.main(
        ["--entries-dir", str(tmp_path), "--changed", "2026-01-02__new__row.yaml"]
    ) == 1
