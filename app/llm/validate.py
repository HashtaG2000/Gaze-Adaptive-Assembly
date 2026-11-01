"""Validation of LLM output. Invalid output is logged by the caller.

Plan (call 1) - the participant asked for a change on the feedback screens
    - valid JSON matching AnalysisResult (strict types, no extra fields)
    - target_merge_level follows the participant's direction:
        compress: higher than the current level (max 2) | detail: lower | keep: equal
    - keep_detailed_steps: valid base step indices (duplicates removed)
    - empty participant message -> default message (warning)
    Invalid output -> the caller falls back to `fallback_plan` (smallest step in the requested
    direction), because the participant explicitly asked for the change.
    Elements to hide / show come from the participant request, never from the LLM.

Generation (call 2) - GeneratedVersion: per step {covers, text, media}
    - the covers of all steps partition the base steps 0..n-1 in order
    - every group is one base step, one pick+place pair, or (level 2 only) several consecutive
      pairs of the same part; level 0 allows single steps only
    - groups never merge a base step listed in keep_detailed_steps
    - text: 1..max_chars characters, control characters removed
    - media only from the base steps the group covers; at most 1 image + 1 video in multi steps
    Structural fields (type, part, positions, quantity, expected time) are derived by the app,
    so a valid generation can never break the instruction set.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from pydantic import ValidationError

from app.i18n import t
from app.models import (
    CONTENT_ELEMENTS, AnalysisResult, GeneratedVersion, InstructionSet, InstructionSettings, Media,
    ParticipantRequest, Step, ValidationOutcome,
)
from app.models.instruction import clean_text

MAX_MEDIA_PER_STEP = 4


def default_message(direction: str) -> str:
    return t(f"llm_default.{direction}")


def _parse_json(raw: Optional[str]) -> Any:
    if raw is None:
        raise ValueError("no response")
    text = raw.strip()
    if text.startswith("```"):  # tolerate a markdown code fence
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    return json.loads(text)


def _pydantic_errors(exc: ValidationError) -> list[str]:
    return [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]


def allowed_levels(current_level: int, direction: str) -> list[int]:
    if direction == "compress":
        return [lv for lv in (0, 1, 2) if lv > current_level]
    if direction == "detail":
        return [lv for lv in (0, 1, 2) if lv < current_level]
    return [current_level]


def requested_hidden(current: InstructionSettings, request: ParticipantRequest) -> list[str]:
    """Hidden elements after applying the participant's hide/show request."""
    return sorted((set(current.hidden_elements) | set(request.hide)) - set(request.show))


def check_request(current: InstructionSettings, request: ParticipantRequest) -> list[str]:
    """Problems with a participant request (the feedback UI prevents these; kept as a guard)."""
    errors = []
    if not allowed_levels(current.merge_level, request.direction):
        errors.append(f"cannot go '{request.direction}' from merge level {current.merge_level}")
    if set(CONTENT_ELEMENTS) <= set(requested_hidden(current, request)):
        errors.append("text, image and video cannot all be hidden")
    return errors


def target_settings(current: InstructionSettings, request: ParticipantRequest, merge_level: int) -> InstructionSettings:
    return InstructionSettings(merge_level=merge_level, hidden_elements=requested_hidden(current, request))


def fallback_plan(current: InstructionSettings, request: ParticipantRequest, reason: str) -> AnalysisResult:
    """Deterministic plan used when the LLM output is invalid: smallest step in the requested direction."""
    levels = allowed_levels(current.merge_level, request.direction)
    level = min(levels) if request.direction == "compress" else max(levels)
    return AnalysisResult(
        progress_assessment="(fallback - LLM output invalid)", plateau=False, target_merge_level=level,
        keep_detailed_steps=[], participant_message=default_message(request.direction),
        researcher_reasoning=f"Fallback plan (smallest step in requested direction). Reason: {reason[:1500]}")


# --- call 1 ---------------------------------------------------------------------------------------

def validate_analysis(raw: Optional[str], current: InstructionSettings, request: ParticipantRequest,
                      n_base_steps: int) -> tuple[Optional[AnalysisResult], ValidationOutcome]:
    warnings: list[str] = []
    try:
        data = _parse_json(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        return None, ValidationOutcome(ok=False, errors=[f"not valid JSON: {exc}"])
    try:
        # strict: no type coercion of LLM output (e.g. "yes" is not a boolean)
        result = AnalysisResult.model_validate_json(json.dumps(data), strict=True)
    except ValidationError as exc:
        return None, ValidationOutcome(ok=False, errors=_pydantic_errors(exc))

    allowed = allowed_levels(current.merge_level, request.direction)
    if result.target_merge_level not in allowed:
        return None, ValidationOutcome(ok=False, errors=[
            f"target_merge_level {result.target_merge_level} not allowed for '{request.direction}' "
            f"from level {current.merge_level} (allowed: {allowed})"])
    bad = [i for i in result.keep_detailed_steps if not 0 <= i < n_base_steps]
    if bad:
        return None, ValidationOutcome(ok=False, errors=[f"keep_detailed_steps out of range: {bad}"])
    result = result.model_copy(update={
        "keep_detailed_steps": sorted(set(result.keep_detailed_steps)),
        "participant_message": clean_text(result.participant_message),
    })
    if not result.participant_message:
        warnings.append("empty participant_message -> default message used")
        result = result.model_copy(update={"participant_message": default_message(request.direction)})
    return result, ValidationOutcome(ok=True, warnings=warnings)


# --- call 2 ---------------------------------------------------------------------------------------

def group_kind(base: InstructionSet, covers: list[int]) -> Optional[str]:
    """'pick' | 'place' | 'pick_place' | 'pick_place_multi' for a legal group, else None."""
    steps = base.steps
    if len(covers) == 1:
        return steps[covers[0]].type
    if len(covers) % 2 or steps[covers[0]].type != "pick":
        return None
    part = steps[covers[0]].part_id
    for k in range(0, len(covers), 2):
        pick, place = steps[covers[k]], steps[covers[k + 1]]
        if pick.type != "pick" or place.type != "place" or pick.part_id != part or place.part_id != part:
            return None
    return "pick_place" if len(covers) == 2 else "pick_place_multi"


def build_step(base: InstructionSet, index: int, covers: list[int], text: str, media: list[Media]) -> Step:
    steps = [base.steps[i] for i in covers]
    kind = group_kind(base, covers)
    if kind is None:
        raise ValueError(f"illegal group {covers}")
    positions = [p for s in steps for p in s.position_ids]
    quantity = max(1, len(covers) // 2) if kind in ("pick_place", "pick_place_multi") else steps[0].quantity
    return Step(index=index, type=kind, text=text, media=media, interaction=steps[0].interaction,
                expected_time_s=round(sum(s.expected_time_s for s in steps), 3), part_id=steps[0].part_id,
                quantity=quantity, position_ids=positions, covers=list(covers),
                reference_image=steps[-1].reference_image)   # board state after the last combined step


def validate_generation(raw: Optional[str], base: InstructionSet, target: InstructionSettings,
                        keep_detailed: list[int], version_level: int, max_chars: int,
                        set_id: Optional[str] = None, title: Optional[str] = None,
                        ) -> tuple[Optional[InstructionSet], ValidationOutcome]:
    try:
        data = _parse_json(raw)
        gen = GeneratedVersion.model_validate_json(json.dumps(data), strict=True)
    except (ValueError, json.JSONDecodeError) as exc:
        return None, ValidationOutcome(ok=False, errors=[f"not valid JSON: {exc}"])
    except ValidationError as exc:
        return None, ValidationOutcome(ok=False, errors=_pydantic_errors(exc))

    errors: list[str] = []
    warnings: list[str] = []
    n = base.n_steps
    flat = [i for s in gen.steps for i in s.covers]
    if flat != list(range(n)):
        errors.append(f"steps must cover base steps 0..{n - 1} exactly once and in order, got {flat}")
    keep = set(keep_detailed)
    level = target.merge_level
    built: list[Step] = []
    for idx, g in enumerate(gen.steps):
        c = g.covers
        if c != list(range(c[0], c[0] + len(c))) or not all(0 <= i < n for i in c):
            errors.append(f"step {idx + 1}: covers {c} must be consecutive valid base steps")
            continue
        kind = group_kind(base, c)
        if kind is None:
            errors.append(f"step {idx + 1}: base steps {c} cannot be combined")
            continue
        if level == 0 and len(c) > 1:
            errors.append(f"step {idx + 1}: merge level 0 allows no combined steps")
        if level == 1 and kind == "pick_place_multi":
            errors.append(f"step {idx + 1}: combining several pairs needs merge level 2")
        if len(c) > 1 and keep & set(c):
            errors.append(f"step {idx + 1}: base steps {sorted(keep & set(c))} must stay detailed")
        text = clean_text(g.text)
        if not text:
            errors.append(f"step {idx + 1}: empty text")
        elif len(text) > max_chars:
            errors.append(f"step {idx + 1}: text longer than {max_chars} characters")
        available = {m.path: m for i in c for m in base.steps[i].media}
        foreign = [p for p in g.media if p not in available]
        if foreign:
            errors.append(f"step {idx + 1}: media not from its base steps: {foreign}")
        media = [available[p] for p in dict.fromkeys(g.media) if p in available]   # de-duplicated by path
        if len(media) > MAX_MEDIA_PER_STEP:
            errors.append(f"step {idx + 1}: at most {MAX_MEDIA_PER_STEP} media per step")
        if kind == "pick_place_multi" and (sum(m.type == "image" for m in media) > 1
                                          or sum(m.type == "video" for m in media) > 1):
            errors.append(f"step {idx + 1}: combined repeat steps show at most 1 image and 1 video")
        if not errors:
            built.append(build_step(base, idx, c, text, media))

    if errors:
        return None, ValidationOutcome(ok=False, errors=errors)
    if level > 0 and all(len(s.covers) == 1 for s in built):
        warnings.append("no step was combined (all steps kept detailed)")
    try:
        new = InstructionSet(id=set_id or base.id, title=title or base.title, version_level=version_level,
                             settings=target, steps=built)
    except ValidationError as exc:
        return None, ValidationOutcome(ok=False, errors=_pydantic_errors(exc))
    return new, ValidationOutcome(ok=True, warnings=warnings)


def structure_key(iset: InstructionSet) -> tuple:
    """Hashable fingerprint of a version (to detect identical versions)."""
    return (iset.settings.merge_level, tuple(iset.settings.hidden_elements),
            tuple((tuple(s.covered()), s.text) for s in iset.steps))
