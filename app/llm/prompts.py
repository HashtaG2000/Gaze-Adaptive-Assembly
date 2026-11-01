"""Prompt construction for the two LLM calls.

Prompt wording lives in data/prompts/*.txt ($placeholders, string.Template) and the rules in
data/rules/analysis_rules.json - both editable without touching code and re-read when changed.
Each builder returns (prompt, schema, payload); the payload is the structured input
(also embedded in the prompt, and used directly by the mock client).
"""
from __future__ import annotations

import json
from pathlib import Path
from string import Template
from typing import Any, Optional

from app.core.files import FileCache, read_text
from app.llm.validate import allowed_levels, requested_hidden
from app.models import (
    AnalysisResult, GeneratedVersion, InstructionSet, InstructionSettings, ParticipantRequest, RunSummary,
)
from app.pipeline.summary import summary_for_condition

DIRECTION_DOC = {
    "compress": "fewer, combined steps (shorter instructions)",
    "keep": "the same steps as now (only the shown elements change)",
    "detail": "more, smaller steps (more detailed instructions)",
}

_templates: FileCache[str] = FileCache(lambda p: read_text(p, max_bytes=512 * 1024), max_entries=16)


class PromptTemplates:
    def __init__(self, directory: Path) -> None:
        self.dir = Path(directory)

    def get(self, name: str) -> str:
        try:
            return _templates.get(self.dir / f"{name}.txt")
        except OSError as exc:
            raise FileNotFoundError(f"prompt template '{name}.txt' missing in {self.dir}") from exc

    def render(self, name: str, **values: Any) -> str:
        return Template(self.get(name)).safe_substitute({k: str(v) for k, v in values.items()})


def _base_steps(base: InstructionSet, descriptions: Optional[dict[str, str]] = None) -> list[dict[str, Any]]:
    descriptions = descriptions or {}
    return [{
        "index": s.index, "type": s.type, "part_id": s.part_id, "position_ids": s.position_ids,
        "text": s.text, "expected_time_s": s.expected_time_s,
        "media": [{"path": m.path, "type": m.type, "role": m.role, "description": descriptions.get(m.path, "")}
                  for m in s.media],
    } for s in base.steps]


def build_analysis_prompt(
    templates: PromptTemplates,
    rules: str,
    condition: str,
    base: InstructionSet,
    current: InstructionSet,
    summaries: list[RunSummary],
    request: ParticipantRequest,
    previous_requests: list[dict[str, Any]],
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    payload = {
        "condition": condition,
        "base_steps": [{"index": s.index, "type": s.type, "part_id": s.part_id, "text": s.text} for s in base.steps],
        "current_version_level": current.version_level,
        "current_settings": current.settings.model_dump(mode="json"),
        "current_steps": [{"step_index": s.index, "covers_base_steps": s.covered(), "text": s.text}
                          for s in current.steps],
        "participant_request": request.model_dump(mode="json"),
        "allowed_target_merge_levels": allowed_levels(current.settings.merge_level, request.direction),
        "resulting_hidden_elements": requested_hidden(current.settings, request),
        "previous_requests": previous_requests,
        "run_summaries": [summary_for_condition(s, condition) for s in summaries],
    }
    schema = AnalysisResult.model_json_schema()
    data_doc = templates.get("fields_timing") + "\n" + templates.get(
        "fields_gaze" if condition == "gaze" else "fields_time_only")
    prompt = templates.render(
        "analysis", rules=rules, condition=condition, data_doc=data_doc,
        difficulty=request.difficulty or "not given", direction=request.direction,
        direction_doc=DIRECTION_DOC[request.direction], hide=request.hide or "none", show=request.show or "none",
        n_runs=len(summaries), payload=json.dumps(payload, ensure_ascii=False, indent=1),
        schema=json.dumps(schema, ensure_ascii=False))
    return prompt, schema, payload


def build_generation_prompt(
    templates: PromptTemplates,
    base: InstructionSet,
    target: InstructionSettings,
    keep_detailed: list[int],
    request: Optional[ParticipantRequest],
    max_chars: int,
    descriptions: Optional[dict[str, str]] = None,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    payload = {
        "base_steps": _base_steps(base, descriptions),
        "target_settings": target.model_dump(mode="json"),
        "keep_detailed_steps": keep_detailed,
        "participant_request": request.model_dump(mode="json") if request else None,
        "max_text_chars": max_chars,
    }
    schema = GeneratedVersion.model_json_schema()
    prompt = templates.render(
        "generation", merge_level=target.merge_level, keep_detailed=keep_detailed or "none",
        max_chars=max_chars, difficulty=(request.difficulty if request else None) or "not given",
        direction=request.direction if request else "compress", hidden=target.hidden_elements or "none",
        payload=json.dumps(payload["base_steps"], ensure_ascii=False, indent=1),
        schema=json.dumps(schema, ensure_ascii=False))
    return prompt, schema, payload
