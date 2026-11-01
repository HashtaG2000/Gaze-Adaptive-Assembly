"""Export all data of one session as CSV + JSON (cockpit "Export package").

Reads the session folder (so it also works for archived sessions):
    export_<timestamp>/
        step_metrics.csv   one row per run x step (dwell % flattened to dwell_<aoi>)
        runs.csv           one row per run (run, repetition, trial flag, totals, version, settings)
        gaze.csv           every gaze sample
        events.csv         step events and state transitions
        decisions.csv      participant decisions
        observations.csv   observer verification per step
        calibration.csv    eye-tracker checks
        llm_calls.csv      LLM calls (prompt / raw response included)
        session_data.json  everything above except gaze, plus session meta, survey and all versions
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from app.core.files import read_json, write_json_atomic
from app.models import ALL_AOIS

MAX_JSONL_LINE = 5 * 1024 * 1024


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL file; corrupted or oversized lines (e.g. after a crash) are skipped."""
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip() or len(line) > MAX_JSONL_LINE:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
    return out


def _read_optional_json(path: Path) -> Any:
    try:
        return read_json(path) if path.exists() else None
    except (OSError, ValueError):
        return None


def flatten_runs(summaries: list[dict[str, Any]]) -> tuple[pd.DataFrame, pd.DataFrame]:
    step_rows, run_rows = [], []
    for s in summaries:
        base = {"run": s.get("run"), "repetition": s.get("repetition"), "is_trial": s.get("is_trial", False),
                "version_level": s.get("version_level"), "merge_level": s.get("settings", {}).get("merge_level"),
                "hidden_elements": "|".join(s.get("settings", {}).get("hidden_elements", []))}
        run_rows.append({**base, **s.get("totals", {}), "n_steps": len(s.get("steps", []))})
        for st in s.get("steps", []):
            row = {**base, **{k: v for k, v in st.items() if k not in ("dwell_pct", "transitions")}}
            row["transitions"] = " > ".join(st.get("transitions", []))
            for aoi in ALL_AOIS:
                row[f"dwell_{aoi}"] = st.get("dwell_pct", {}).get(aoi)
            step_rows.append(row)
    return pd.DataFrame(step_rows), pd.DataFrame(run_rows)


def _flat(records: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.json_normalize(records) if records else pd.DataFrame()


def export_session(session_dir: Path, out_root: Optional[Path] = None) -> Path:
    session_dir = Path(session_dir)
    out = (out_root or session_dir) / f"export_{datetime.now():%Y%m%d_%H%M%S_%f}"
    out.mkdir(parents=True, exist_ok=False)

    meta = _read_optional_json(session_dir / "session.json") or {}
    summaries = _read_optional_json(session_dir / "summaries.json") or []
    if not isinstance(summaries, list):
        summaries = []
    survey = _read_optional_json(session_dir / "survey.json")
    tables = {name: read_jsonl(session_dir / f"{name}.jsonl")
              for name in ("events", "decisions", "observations", "calibration", "llm_calls", "verifications")}
    # a reviewed camera check is logged twice; the export keeps its final state
    tables["verifications"] = list({r.get("id"): r for r in tables["verifications"]}.values())
    versions = [v for v in (_read_optional_json(p) for p in sorted((session_dir / "versions").glob("*.json")))
                if v is not None] if (session_dir / "versions").is_dir() else []

    steps_df, runs_df = flatten_runs(summaries)
    steps_df.to_csv(out / "step_metrics.csv", index=False)
    runs_df.to_csv(out / "runs.csv", index=False)
    gaze = read_jsonl(session_dir / "gaze.jsonl")
    pd.DataFrame(gaze).to_csv(out / "gaze.csv", index=False)
    for name, records in tables.items():
        _flat(records).to_csv(out / f"{name}.csv", index=False)

    bundle = {"session": meta, "run_summaries": summaries, "survey": survey, "versions": versions,
              **tables, "gaze_samples": {"file": "gaze.csv", "count": len(gaze)}}
    write_json_atomic(out / "session_data.json", bundle)
    return out
