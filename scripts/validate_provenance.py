#!/usr/bin/env python3
"""Verify leaderboard entry provenance against the public Harbor Hub.

The offline validator (scripts/validate_leaderboard.py) checks only what is
expressed in the committed files: the header's job UUID and Hub URL are
submitter-supplied strings that merely have to match each other. This stage
closes that gap by fetching each entry's public Hub job page and comparing the
entry's claims against the job record the Hub itself serves.

No credentials are required: every check below reads the public job page only,
so the stage also runs for fork pull requests (GitHub does not pass secrets to
pull_request workflows). Sweeping the complete trial list of a job (full
trial-ownership and per-task cohort checks) needs a read-capable
HARBOR_API_KEY and is deliberately out of scope here — see issue #168.

What is verified per entry, from the page's embedded job record:

  1. The job exists and is publicly visible (HTTP 200, visibility == "public").
  2. n_trials equals the job's recorded trial count.
  3. accuracy, reconciled via implied passing-trial counts (the Hub's
     recorded average reward covers only trials that carry a reward, and that
     count is not exposed; the entry convention scores unscored trials 0 and
     keeps them in the denominator — see leaderboard.yaml's metric
     definitions).
  4. The token breakdown and cost match the job's recorded usage.
  5. Cohort shape: recorded attempts per task and total trials match the
     expected cohort configured in leaderboard.yaml (provenance.cohort).
  6. Judge configuration matches the expected judge, when the page exposes it
     (compared by model name; the record may or may not carry a provider).
  7. The run path references the expected dataset family (the page does not
     expose the dataset revision, so this check is warn-only), and the claimed
     trial ids against the page's embedded trial listing. The page embeds
     only the first page of trials, so an absent id is inconclusive — except
     for the one hard case: a changed entry claiming the job's full trial
     count while sharing none of the visible ids fails outright.

Every metric comparison requires the job record to actually carry the field
being compared (n_total_trials, stats.avg_reward, tokens, cost); a record
missing required evidence makes the entry inconclusive, never a silent pass,
and the record's own job id must match the requested UUID.

Severity policy (migration-safe):

  - Entries listed with --changed / --changed-from (the ones a PR adds or
    modifies): provenance failures are errors (exit code 1).
  - All other (legacy) entries, and every entry when --legacy is passed:
    failures are warnings only (exit code 0).
  - Hub downtime and transient service responses (429/5xx), page-shape
    changes, missing required evidence, and unreached Hub are all
    "inconclusive": reported as warnings for every entry and never fail CI.
    Human sign-off happens in PR review.

The expected cohort, judge, dataset, and comparison tolerances are read from
leaderboard/leaderboard.yaml (provenance block) so this stage and the Hub
leaderboard config share one source of truth.
"""

from __future__ import annotations

import argparse
import functools
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Provenance checks reuse the header parser of the offline validator so both
# stages read the same "Source job:" line the same way.
from validate_leaderboard import CONFIG_PATH, ENTRIES_DIR, JOB_UUID_RE, ROOT  # noqa: E402

HUB_JOB_PATH = "/jobs/{job_id}"
USER_AGENT = "enterprise-bench-leaderboard-provenance/1.0"
PAGE_TIMEOUT_SECONDS = 20
FETCH_ATTEMPTS = 2

# Next.js App Router streams the page's data as flight chunks: each
# <script>self.__next_f.push([1,"..."])</script> carries a JSON string that
# concatenates into the flight text, which itself embeds the job record.
_FLIGHT_CHUNK_RE = re.compile(r'self\.__next_f\.push\(\[1,(".*?")\]\)', re.S)
# Trial rows embedded in the flight text: {"id":"<uuid>","config_values":{...}}
_EMBEDDED_TRIAL_RE = re.compile(r'\{"id":"([0-9a-fA-F-]{36})","config_values"')

DEFAULT_CONFIG: dict[str, Any] = {
    "hub_base_url": "https://hub.harborframework.com",
    "expected": {
        "dataset": None,  # informational; falls back to leaderboard.yaml's package
        "dataset_stem": "l1-l2-bench",  # run path must reference the dataset family
        "judge": "gpt-6-luna",
        "cohort": {"tasks": 42, "attempts": 5, "trials": 210},
    },
    "tolerances": {
        "passes": 2,  # max |implied passing trials difference| (see below)
        "tokens_pct": 0.5,  # percent
        "cost_pct": 0.5,  # percent
    },
}

# Transient service responses: never evidence about the entry, always
# inconclusive (Hub downtime must not produce a red X).
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

# Evidence the job record must carry before any metric comparison counts:
# without these the stage must report inconclusive instead of silently
# passing an entry it could not actually verify. Every entry must be a
# number (compared through _as_number), not merely present.
REQUIRED_JOB_EVIDENCE = {
    "n_total_trials": ("n_total_trials",),
    "average reward": ("stats", "avg_reward"),
    "input_tokens": ("input_tokens",),
    "output_tokens": ("output_tokens",),
    "cache_tokens": ("cache_tokens",),
    "cost_usd": ("cost_usd",),
}


class FetchHTTPError(Exception):
    """The Hub answered, but not with a success status."""

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status


class FetchNetworkError(Exception):
    """The Hub could not be reached (DNS, TLS, timeout, …)."""


def load_provenance_config(errors: list[str]) -> dict[str, Any]:
    """Read the provenance contract from leaderboard.yaml (single source of truth)."""
    config: dict[str, Any] = {}
    try:
        config = yaml.safe_load(CONFIG_PATH.read_text()) or {}
    except Exception as exc:
        errors.append(f"leaderboard.yaml parse failed: {exc}")
    prov = config.get("provenance") or {}
    merged = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    for key, value in prov.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    expected = merged.setdefault("expected", {})
    if not expected.get("dataset"):
        expected["dataset"] = config.get("package")
    return merged


def fetch_job_page(url: str, timeout: float = PAGE_TIMEOUT_SECONDS) -> tuple[int, str]:
    """Fetch a Hub job page. Returns (status, body); raises on unreachable Hub.

    Transient service responses (429/5xx) are retried once and then returned
    for the caller to classify as inconclusive; definitive statuses (404, 403,
    …) are returned immediately.
    """
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
    )
    last_error: Exception | None = None
    status: int | None = None
    body = ""
    for attempt in range(FETCH_ATTEMPTS):
        retry = False
        try:
            response = urllib.request.urlopen(request, timeout=timeout)
            with response:
                status, body = response.status, response.read().decode(
                    "utf-8", "replace"
                )
            if status in RETRYABLE_STATUS and attempt + 1 < FETCH_ATTEMPTS:
                retry = True
        except urllib.error.HTTPError as exc:
            # The Hub answered: a non-200 status is evidence, not downtime —
            # except for the transient service codes, which are retried.
            status = exc.code
            body = exc.read().decode("utf-8", "replace") if exc.fp else ""
            if status in RETRYABLE_STATUS and attempt + 1 < FETCH_ATTEMPTS:
                retry = True
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            retry = attempt + 1 < FETCH_ATTEMPTS
        if retry:
            time.sleep(1.5)
            continue
        if status is not None:
            return status, body
    raise FetchNetworkError(str(last_error))


_JOB_RECORD_RE = re.compile(r'"job"\s*:\s*')


def extract_job_record(html: str) -> dict[str, Any] | None:
    """Pull the job record out of the page's flight data, tolerantly."""
    try:
        text = "".join(
            json.loads(chunk)
            for chunk in _FLIGHT_CHUNK_RE.findall(html)
            if chunk.startswith('"')
        )
        match = _JOB_RECORD_RE.search(text)
        if not match:
            return None
        job, _ = json.JSONDecoder().raw_decode(text, match.end())
        if isinstance(job, dict) and job.get("id"):
            return job
    except (ValueError, TypeError, json.JSONDecodeError):
        pass
    return None


def embedded_trial_ids(html: str) -> set[str]:
    """Trial ids embedded in the page's (first-page) trial listing, if any."""
    try:
        text = "".join(
            json.loads(chunk)
            for chunk in _FLIGHT_CHUNK_RE.findall(html)
            if chunk.startswith('"')
        )
    except (ValueError, TypeError, json.JSONDecodeError):
        return set()
    return set(_EMBEDDED_TRIAL_RE.findall(text))


def _pct_diff(actual: float, expected: float) -> float:
    if not expected:
        return 0.0 if actual == expected else float("inf")
    return abs(actual - expected) / abs(expected) * 100.0


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _lookup(obj: Any, path: tuple[str, ...]) -> Any:
    """Resolve a dotted evidence path, returning None anywhere it breaks."""
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _mapping(value: Any) -> dict[str, Any]:
    """Coerce an expected mapping into {} when the Hub sends another shape.

    Page-shape changes must degrade to inconclusive findings, never crash the
    stage with an AttributeError on an unexpected .get().
    """
    return value if isinstance(value, dict) else {}


def _changed_set(names: list[str]) -> set[str]:
    """Normalize changed-entry references to entry file names.

    git diff emits repository-relative paths (leaderboard/entries/<file.yaml)
    while this CLI compares against the entry's file name, so every reference
    is reduced to its basename.
    """
    return {Path(name.strip()).name for name in names if name.strip()}


def check_metric(
    findings: list[dict[str, str]],
    check: str,
    label: str,
    actual: float | None,
    expected: float | None,
    tolerance: str,
    config: dict[str, Any],
) -> None:
    """Record a mismatch as an error; a missing side stays silent."""
    if actual is None or expected is None:
        return
    if _pct_diff(actual, expected) > config["tolerances"][tolerance]:
        findings.append(
            {
                "level": "error",
                "check": check,
                "message": f"{label}: entry claims {actual} but the Hub job "
                f"records {expected}",
            }
        )


def _normalize_rows(
    rows: list[Any],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Tolerate malformed entry rows instead of crashing, but say so.

    A non-mapping row, a non-mapping/empty metrics block, or missing/empty/
    non-list trial_ids must degrade that row's checks — never silently: each
    such row emits a row_shape finding so standalone runs cannot report a
    confident pass over an entry it did not really examine. (The offline
    validator hard-fails these same shapes in CI.)
    """
    normalized: list[dict[str, Any]] = []
    shape_findings: list[dict[str, str]] = []
    for index, row in enumerate(rows):
        problems: list[str] = []
        if not isinstance(row, dict):
            normalized.append({"metrics": {}, "trial_ids": []})
            shape_findings.append(
                {
                    "level": "warn",
                    "check": "row_shape",
                    "message": f"row {index} is not a mapping; its checks were "
                    "skipped (the offline validator rejects this entry)",
                }
            )
            continue
        metrics = row.get("metrics")
        trial_ids = row.get("trial_ids")
        if not metrics:
            problems.append("metrics is missing or empty")
        elif not isinstance(metrics, dict):
            problems.append("metrics is not a mapping")
        if not trial_ids:
            problems.append("trial_ids is missing or empty")
        elif not isinstance(trial_ids, list):
            problems.append("trial_ids is not a list")
        if problems:
            shape_findings.append(
                {
                    "level": "warn",
                    "check": "row_shape",
                    "message": f"row {index}: {'; '.join(problems)}; affected "
                    "checks were skipped (the offline validator rejects this "
                    "entry)",
                }
            )
        normalized.append(
            {
                "metrics": metrics if isinstance(metrics, dict) else {},
                "trial_ids": trial_ids if isinstance(trial_ids, list) else [],
            }
        )
    return normalized, shape_findings


def verify_entry(
    entry: str,
    text: str,
    data: Any,
    changed: bool,
    config: dict[str, Any],
    fetcher: Callable[..., tuple[int, str]],
) -> dict[str, Any]:
    """Verify one entry file against its Hub job page."""
    findings: list[dict[str, str]] = []
    # Tolerate a malformed entry document: a non-mapping document or a
    # missing/non-list 'rows' key must degrade the entry's checks, not crash.
    rows: list[Any] = []
    if not isinstance(data, dict):
        findings.append(
            {
                "level": "warn",
                "check": "row_shape",
                "message": "entry is not a YAML mapping; its rows could not be "
                "checked (the offline validator rejects this entry)",
            }
        )
    elif not isinstance(data.get("rows"), list) or not data["rows"]:
        findings.append(
            {
                "level": "warn",
                "check": "row_shape",
                "message": "entry has no rows to verify (the 'rows' list is "
                "missing, empty, or not a list); its claims could not be "
                "checked (the offline validator rejects this entry)",
            }
        )
    else:
        rows, shape_findings = _normalize_rows(data["rows"])
        findings.extend(shape_findings)
    uuid_match = JOB_UUID_RE.search(text)
    if not uuid_match:
        findings.append(
            {
                "level": "error" if changed else "warn",
                "check": "header",
                "message": "no 'Source job: <uuid>' header comment to verify",
            }
        )
        return _result(entry, uuid_match and uuid_match.group(1), changed, findings)

    job_id = uuid_match.group(1)
    url = config["hub_base_url"] + HUB_JOB_PATH.format(job_id=job_id)

    try:
        status, html = fetcher(url)
    except FetchNetworkError as exc:
        findings.append(
            {
                "level": "inconclusive",
                "check": "fetch",
                "message": f"could not reach {url} ({exc}); needs human sign-off",
            }
        )
        return _result(entry, job_id, changed, findings)

    if status in RETRYABLE_STATUS:
        findings.append(
            {
                "level": "inconclusive",
                "check": "fetch",
                "message": f"Hub answered HTTP {status} for {url} (transient "
                "service response after retry); needs human sign-off",
            }
        )
        return _result(entry, job_id, changed, findings)

    if status != 200:
        findings.append(
            {
                "level": "error" if changed else "warn",
                "check": "visibility",
                "message": f"job page {url} returned HTTP {status}; the job does "
                "not exist or is not publicly visible",
            }
        )
        return _result(entry, job_id, changed, findings)

    job = extract_job_record(html)
    if job is None:
        findings.append(
            {
                "level": "inconclusive",
                "check": "parse",
                "message": "page returned 200 but no job record could be parsed "
                "(Hub page format may have changed); needs human sign-off",
            }
        )
        return _result(entry, job_id, changed, findings)

    # The record must be about the requested job. UUIDs are compared
    # case-insensitively, matching the offline validator's tolerance.
    if str(job.get("id")).lower() != job_id.lower():
        findings.append(
            {
                "level": "error" if changed else "warn",
                "check": "identity",
                "message": f"the page for job {job_id} carries the record of job "
                f"{job.get('id')!r}",
            }
        )
        return _result(entry, job_id, changed, findings)

    # Required evidence: each field must be present AND numeric — a
    # non-numeric value means the comparison could not run, so the entry must
    # not be reported as verified.
    missing_evidence = [
        label
        for label, path in REQUIRED_JOB_EVIDENCE.items()
        if _as_number(_lookup(job, path)) is None
    ]
    if missing_evidence:
        findings.append(
            {
                "level": "inconclusive",
                "check": "evidence",
                "message": f"job record is missing or has non-numeric required "
                f"fields ({', '.join(missing_evidence)}); the entry was not "
                "fully verified; needs human sign-off",
            }
        )

    # 1. Publicly visible.
    visibility = job.get("visibility")
    if visibility and visibility != "public":
        findings.append(
            {
                "level": "error" if changed else "warn",
                "check": "visibility",
                "message": f"job visibility is {visibility!r}, not public",
            }
        )

    # 2. Trial count.
    n_total = _as_number(job.get("n_total_trials"))
    row_counts = [_as_number(row.get("metrics", {}).get("n_trials")) for row in rows]
    # An entry with no rows claims nothing; the row_shape warning covers it.
    if rows and n_total is not None and all(count is not None for count in row_counts):
        claimed = sum(row_counts)  # type: ignore[type-var]
        if claimed != n_total:
            findings.append(
                {
                    "level": "error" if changed else "warn",
                    "check": "n_trials",
                    "message": f"entry claims {int(claimed)} trials but the Hub job "
                    f"records {int(n_total)}",
                }
            )

    # 3. Accuracy vs the job's recorded average reward.
    #
    # Hub's job-level avg_reward averages only the trials that carry a
    # recorded reward, and that count is not exposed anywhere on the page
    # (the GLM job: 50/209 vs the entry's 50/210, with 210 visible counters).
    # Comparing percentages directly therefore flags correct entries. The
    # stable quantity on both sides is the number of PASSING trials, so
    # compare implied pass counts, tolerating the unexposed unscored gap.
    stats = _mapping(job.get("stats"))
    avg_reward = _as_number(stats.get("avg_reward"))
    if avg_reward is not None and n_total is not None:
        hub_passes = avg_reward * n_total
        entry_passes = 0.0
        have_entry_passes = False
        for row in rows:
            accuracy = _as_number(row["metrics"].get("accuracy"))
            row_trials = _as_number(row["metrics"].get("n_trials"))
            if accuracy is not None and row_trials is not None:
                entry_passes += accuracy * row_trials / 100.0
                have_entry_passes = True
        if have_entry_passes:
            tolerance = max(
                float(config["tolerances"]["passes"]), 0.01 * n_total
            )
            if abs(entry_passes - hub_passes) > tolerance:
                findings.append(
                    {
                        "level": "error" if changed else "warn",
                        "check": "accuracy",
                        "message": f"the entry implies {entry_passes:.2f} passing "
                        f"trials but the job records {hub_passes:.2f} "
                        f"(avg reward {avg_reward} over its scored trials); "
                        "allowed difference is "
                        f"{tolerance:.2f} trials",
                    }
                )

    # 4. Token breakdown and cost vs the job's recorded usage.
    job_input = _as_number(job.get("input_tokens"))
    job_output = _as_number(job.get("output_tokens"))
    job_cache = _as_number(job.get("cache_tokens"))
    for row in rows:
        metrics = row.get("metrics", {})
        uncached = _as_number(metrics.get("uncached_input_tokens"))
        cached = _as_number(metrics.get("cached_input_tokens"))
        output = _as_number(metrics.get("output_tokens"))
        total = _as_number(metrics.get("total_tokens"))
        if job_input is not None and uncached is not None and cached is not None:
            check_metric(
                findings, "tokens", "uncached_input_tokens + cached_input_tokens",
                uncached + cached, job_input, "tokens_pct", config,
            )
        if job_output is not None and output is not None:
            check_metric(
                findings, "tokens", "output_tokens", output, job_output,
                "tokens_pct", config,
            )
        if job_cache is not None and cached is not None:
            check_metric(
                findings, "tokens", "cached_input_tokens", cached, job_cache,
                "tokens_pct", config,
            )
        if job_input is not None and job_output is not None and total is not None:
            check_metric(
                findings, "tokens", "total_tokens", total, job_input + job_output,
                "tokens_pct", config,
            )
        cost = _as_number(metrics.get("total_cost_usd"))
        job_cost = _as_number(job.get("cost_usd"))
        if cost is not None and job_cost is not None:
            check_metric(
                findings, "cost", "total_cost_usd", cost, job_cost, "cost_pct", config,
            )

    # 5. Cohort shape (attempts per task, expected total trials).
    job_config = _mapping(job.get("config"))
    expected_cohort = (config["expected"].get("cohort") or {})
    expected_trials = _as_number(expected_cohort.get("trials"))
    if n_total is not None and expected_trials is not None and n_total != expected_trials:
        findings.append(
            {
                "level": "error" if changed else "warn",
                "check": "cohort",
                "message": f"job recorded {int(n_total)} trials; the expected cohort "
                f"is {int(expected_trials)} "
                f"({expected_cohort.get('tasks')} tasks x "
                f"{expected_cohort.get('attempts')} attempts)",
            }
        )
    n_attempts = _as_number(job_config.get("n_attempts"))
    expected_attempts = _as_number(expected_cohort.get("attempts"))
    if n_attempts is None:
        findings.append(
            {
                "level": "inconclusive",
                "check": "cohort",
                "message": "job record does not expose attempts per task; "
                "needs human sign-off",
            }
        )
    elif expected_attempts is not None and n_attempts != expected_attempts:
        findings.append(
            {
                "level": "error" if changed else "warn",
                "check": "cohort",
                "message": f"job ran {int(n_attempts)} attempts per task; expected "
                f"{int(expected_attempts)}",
            }
        )

    # 6. Judge configuration. The record may carry a bare model name or a
    # provider-qualified one; compare the model name either way.
    def judge_name(judge: Any) -> str | None:
        return judge.rsplit("/", 1)[-1] if isinstance(judge, str) else None

    expected_judge = config["expected"].get("judge")
    verifier_env = _mapping(_mapping(job_config.get("verifier")).get("env"))
    judge_model = verifier_env.get("JUDGE_MODEL")
    if judge_model is None:
        findings.append(
            {
                "level": "inconclusive",
                "check": "judge",
                "message": "job record does not expose the judge model; "
                "needs human sign-off",
            }
        )
    elif (
        expected_judge
        and judge_name(judge_model) != judge_name(expected_judge)
    ):
        findings.append(
            {
                "level": "error" if changed else "warn",
                "check": "judge",
                "message": f"job judged by {judge_model!r}; expected "
                f"{expected_judge!r}",
            }
        )

    # 7. Dataset. The job record exposes the path the run was invoked with,
    # not the dataset revision, so this is a stem-level, warn-only check:
    # verifying the exact dataset revision needs an authenticated read
    # (issue #168 / #147).
    dataset_stem = config["expected"].get("dataset_stem")
    datasets_raw = job_config.get("datasets")
    datasets_raw = datasets_raw if isinstance(datasets_raw, list) else []
    dataset_paths = [
        entry_path.get("path")
        for entry_path in datasets_raw
        if isinstance(entry_path, dict)
    ]
    dataset_paths = [path for path in dataset_paths if isinstance(path, str)]
    if not dataset_paths:
        findings.append(
            {
                "level": "inconclusive",
                "check": "dataset",
                "message": "job record does not expose its dataset; "
                "needs human sign-off",
            }
        )
    elif dataset_stem and not any(dataset_stem in path for path in dataset_paths):
        findings.append(
            {
                "level": "warn",
                "check": "dataset",
                "message": f"job was run from path(s) {dataset_paths}, which do not "
                f"reference {dataset_stem!r}; the dataset revision cannot be "
                "verified from the public page",
            }
        )

    # 7b. Trial membership. The page embeds only the first page of the job's
    # trials, so absence alone is inconclusive. The one hard case: a changed
    # entry that claims the job's full trial count yet shares NONE of the
    # visible ids cannot be describing this job's trials — that is the
    # copy-real-metrics-plus-arbitrary-uuids bypass, and it fails.
    embedded = embedded_trial_ids(html)
    # UUID comparisons are case-insensitive, matching the offline validator,
    # which accepts uppercase ids in entry files.
    embedded_lower = {trial_id.lower() for trial_id in embedded}
    claimed_ids = [
        trial_id
        for row in rows
        for trial_id in (row.get("trial_ids") or [])
        if isinstance(trial_id, str)
    ]
    if embedded_lower and claimed_ids:
        confirmed = sum(
            1 for trial_id in claimed_ids if trial_id.lower() in embedded_lower
        )
        if confirmed == 0 and n_total is not None and len(claimed_ids) == int(n_total):
            findings.append(
                {
                    "level": "error" if changed else "warn",
                    "check": "trial_ownership",
                    "message": f"none of the {len(claimed_ids)} claimed trial ids "
                    "appear in the job's visible trial listing; the claimed "
                    "trials do not belong to this job",
                }
            )
        elif confirmed < len(claimed_ids):
            findings.append(
                {
                    "level": "inconclusive",
                    "check": "trial_ownership",
                    "message": f"{confirmed} of {len(claimed_ids)} trial ids are "
                    "visible in the page's embedded trial listing (first page "
                    "only); full ownership needs an authenticated read",
                }
            )
        # else: every claimed id is visible on the embedded listing — ownership
        # is confirmed for the claimed subset; nothing to report.
    elif claimed_ids:
        # Symmetric case: valid metrics but no embedded listing means trial
        # ownership could not be checked at all — never a silent pass.
        findings.append(
            {
                "level": "inconclusive",
                "check": "trial_ownership",
                "message": "the job record does not embed its trial listing; "
                "trial ownership could not be checked; needs human sign-off",
            }
        )

    return _result(entry, job_id, changed, findings)


def _result(
    entry: str,
    job_id: str | None,
    changed: bool,
    findings: list[dict[str, str]],
) -> dict[str, Any]:
    """Downgrade failures on legacy rows and derive the entry's status."""
    has_failure = False
    has_warning = False
    for finding in findings:
        if finding["level"] == "inconclusive":
            finding["level"] = "warn"
            finding["message"] = f"[inconclusive] {finding['message']}"
            has_warning = True
        elif finding["level"] == "error" and not changed:
            finding["level"] = "warn"
            finding["message"] = f"[legacy] {finding['message']}"
            has_warning = True
        elif finding["level"] == "error":
            has_failure = True
        else:
            has_warning = True
    return {
        "entry": entry,
        "job_id": job_id,
        "changed": changed,
        "status": "fail" if has_failure else ("warn" if has_warning else "pass"),
        "findings": findings,
    }


def print_results(results: list[dict[str, Any]]) -> None:
    for result in results:
        print(f"- {result['entry']}: {result['status'].upper()}")
        for finding in result["findings"]:
            print(f"  - {finding['check']}: {finding['message']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify leaderboard entry provenance against the public "
        "Harbor Hub (read-only; no credentials)."
    )
    parser.add_argument(
        "--entries-dir",
        type=Path,
        default=None,
        help="Directory containing the entry YAML files to verify. "
        "Defaults to leaderboard/entries in this repository.",
    )
    parser.add_argument(
        "--changed",
        nargs="*",
        default=None,
        help="Entry file names this change adds or modifies; failures on these "
        "are errors. Defaults to every entry (sweep mode). Prefer "
        "--changed-from, which is safe for arbitrary file names.",
    )
    parser.add_argument(
        "--changed-from",
        type=Path,
        default=None,
        help="Read the changed entry names from a NUL-delimited file (safe for "
        "names containing spaces, quotes, commas, or newlines).",
    )
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="Treat every entry as legacy: failures are warnings only.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print a JSON report instead of human-readable lines.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=PAGE_TIMEOUT_SECONDS,
        help="Per-request timeout in seconds (default: 20).",
    )
    args = parser.parse_args(argv)

    if args.entries_dir is None:
        entries_dir = ENTRIES_DIR
    else:
        entries_dir = args.entries_dir.expanduser().resolve()

    errors: list[str] = []
    config = load_provenance_config(errors)
    for error in errors:
        print(f"- {error}")

    try:
        shown_dir = entries_dir.relative_to(ROOT)
    except ValueError:
        shown_dir = entries_dir

    if not entries_dir.exists():
        print(f"Provenance verification failed: missing {shown_dir}")
        return 1

    entry_paths = sorted(p for p in entries_dir.glob("*.yaml") if p.suffix == ".yaml")
    if not entry_paths:
        print(f"Provenance verification: no entries found in {shown_dir}.")
        return 0

    if args.legacy:
        changed_names: set[str] | None = set()
    elif args.changed_from is not None:
        raw = args.changed_from.read_bytes()
        changed_names = _changed_set(raw.decode("utf-8").split("\x00"))
    elif args.changed is None:
        changed_names = None  # sweep mode: every entry is treated as changed
    else:
        changed_names = _changed_set(
            [name for chunk in args.changed for name in chunk.split(",")]
        )

    # Thread the CLI timeout through to every request.
    fetcher: Callable[..., tuple[int, str]] = functools.partial(
        fetch_job_page, timeout=args.timeout
    )

    results = []
    for path in entry_paths:
        if changed_names is not None and path.name not in changed_names:
            changed = False
        else:
            changed = True
        try:
            text = path.read_text()
            data = yaml.safe_load(text) or {}
        except Exception as exc:
            results.append(
                _result(
                    path.name,
                    None,
                    changed,
                    [{"level": "error" if changed else "warn",
                      "check": "parse", "message": f"unreadable entry: {exc}"}],
                )
            )
            continue
        results.append(
            verify_entry(path.name, text, data, changed, config, fetcher)
        )

    # Changed names with no file on disk: the PR deleted the entry (or renamed
    # it). There is nothing to verify, but report it rather than dropping it.
    if changed_names is not None:
        existing_names = {p.name for p in entry_paths}
        for name in sorted(changed_names - existing_names):
            results.append(
                _result(
                    name,
                    None,
                    True,
                    [{"level": "warn", "check": "changed",
                      "message": "entry file was deleted in this change; "
                      "no provenance to verify"}],
                )
            )

    failed = sum(r["status"] == "fail" for r in results)
    warned = sum(r["status"] == "warn" for r in results)

    if args.json:
        report = {
            "hub_base_url": config["hub_base_url"],
            "entries": results,
            "summary": {"passed": len(results) - failed - warned, "warned": warned,
                        "failed": failed},
        }
        print(json.dumps(report, indent=2))
        return 1 if failed else 0

    print(
        "Provenance verification against the public Harbor Hub "
        "(read-only; no credentials):"
    )
    print_results(results)

    if failed:
        print(
            f"Provenance verification failed: {failed} entry(s) with provenance "
            "failures, "
            f"{warned} entry(s) with warnings or inconclusive results."
        )
        return 1
    print(
        "Provenance verification passed: "
        f"{len(results)} entry(s) checked, {warned} entry(s) with warnings or "
        "inconclusive results (human sign-off)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
