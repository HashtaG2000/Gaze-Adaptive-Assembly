"""Deterministic mock LLM for testing without an API key or a local model.

It is a STAND-IN: it follows simple fixed rules instead of reasoning. Use Gemini or Ollama to get
real LLM adaptation.

Plan policy (uses the structured payload, not the prompt):
    target level: compress -> one level more, or the most compact level after a plateau
                  (last two regular runs within PLATEAU_REL_CHANGE); detail -> one level less;
                  keep -> current level
    keep_detailed_steps: base steps of the last regular run with observer errors, back presses
                  or a duration ratio above STRUGGLE_RATIO
Generation: combines pick+place pairs (and at level 2 repeated parts) except kept steps,
            writes combined texts and picks one position image + one method video.
"""
from __future__ import annotations

import json
from typing import Any

from app.i18n import t
from app.llm.client import CallType

PLATEAU_REL_CHANGE = 0.15
STRUGGLE_RATIO = 2.0


class MockLLMClient:
    name = "mock"

    def complete_json(self, call: CallType, prompt: str, schema: dict[str, Any],
                      payload: dict[str, Any]) -> str:
        if call == "analysis":
            return json.dumps(mock_analysis(payload), ensure_ascii=False)
        return json.dumps(mock_generation(payload), ensure_ascii=False)


def _struggled_base_steps(payload: dict[str, Any]) -> list[int]:
    cur_version = payload["current_version_level"]
    covers = {s["step_index"]: s["covers_base_steps"] for s in payload.get("current_steps", [])}
    runs = [s for s in payload["run_summaries"] if s["version_level"] == cur_version and not s["is_trial"]]
    if not runs:
        return []
    out: set[int] = set()
    for st in runs[-1]["steps"]:
        if st.get("observer_errors", 0) or st.get("back_presses", 0) or st.get("duration_vs_expected_ratio", 0) > STRUGGLE_RATIO:
            out.update(covers.get(st["step_index"], []))
    return sorted(out)


def mock_analysis(payload: dict[str, Any]) -> dict[str, Any]:
    direction = payload["participant_request"]["direction"]
    allowed = payload["allowed_target_merge_levels"]
    cur_version = payload["current_version_level"]
    runs = [s for s in payload["run_summaries"] if s["version_level"] == cur_version and not s["is_trial"]]
    durations = [s["totals"]["total_duration_s"] for s in runs]
    plateau = len(durations) >= 2 and abs(durations[-1] - durations[-2]) / max(durations[-2], 1e-6) < PLATEAU_REL_CHANGE
    level = (max(allowed) if plateau else min(allowed)) if direction == "compress" else max(allowed)
    keep = _struggled_base_steps(payload) if direction == "compress" else []
    return {
        "progress_assessment": f"[mock] {len(durations)} run(s) on version {cur_version}; durations {durations}.",
        "plateau": plateau,
        "target_merge_level": level,
        "keep_detailed_steps": keep,
        "participant_message": t(f"mock_llm.{direction}"),
        "researcher_reasoning": f"[mock] direction={direction}, allowed={allowed}, plateau={plateau} -> level {level}; "
                                f"kept detailed: {keep}",
    }


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text


def _pick_media(place: dict[str, Any]) -> list[str]:
    image = next((m["path"] for m in place["media"] if m["type"] == "image"), None)
    video = next((m["path"] for m in place["media"] if m["type"] == "video"), None)
    return [p for p in (image, video) if p]


def mock_generation(payload: dict[str, Any]) -> dict[str, Any]:
    base = payload["base_steps"]
    level = payload["target_settings"]["merge_level"]
    keep = set(payload.get("keep_detailed_steps") or [])
    max_chars = int(payload.get("max_text_chars") or 220)

    # 1) groups of base indices
    groups: list[list[int]] = []
    for i in range(0, len(base) - 1, 2):
        pair = [i, i + 1]
        if level >= 1 and not keep & set(pair):
            if (level == 2 and groups and len(groups[-1]) >= 2 and not keep & set(groups[-1])
                    and base[groups[-1][0]]["part_id"] == base[i]["part_id"]):
                groups[-1] += pair
            else:
                groups.append(pair)
        else:
            groups += [[i], [i + 1]]

    # 2) text + media per group
    steps = []
    for g in groups:
        if len(g) == 1:
            s = base[g[0]]
            steps.append({"covers": g, "text": s["text"], "media": [m["path"] for m in s["media"]]})
            continue
        pick, places = base[g[0]], [base[j] for j in g[1::2]]
        if len(g) == 2:
            text = f"{pick['text'].rstrip('.')} and {_lower_first(places[0]['text'])}"
        else:
            text = f"{len(places)}×: {_lower_first(pick['text'].rstrip('.'))} – " + " / ".join(
                p["text"].rstrip(".") for p in places) + "."
        steps.append({"covers": g, "text": text[:max_chars], "media": _pick_media(places[0])})
    return {"steps": steps}
