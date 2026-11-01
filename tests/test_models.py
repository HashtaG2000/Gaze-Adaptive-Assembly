"""Phase 1 smoke tests: config, sample instruction set and model validators."""
import json

import pytest
from pydantic import ValidationError

from app.config import PROJECT_ROOT, load_config
from app.models import AnalysisResult, InstructionSet, InstructionSettings, Step

BASE_SET = PROJECT_ROOT / "data/instruction_sets/duplo_tower_base.json"


def _base() -> dict:
    return json.loads(BASE_SET.read_text(encoding="utf-8"))


def test_config_loads():
    cfg = load_config()
    assert cfg.screen.width == 1920 and cfg.screen.height == 1080
    assert cfg.input.next_key == "Space"


def test_base_set_valid_and_media_present():
    cfg = load_config()
    iset = InstructionSet.model_validate(_base())
    assert 6 <= iset.n_steps <= 8
    assert iset.version_level == 0 and iset.settings.merge_level == 0
    assert iset.settings.hidden_elements == []
    media_dir = cfg.resolve(cfg.paths.media)
    for s in iset.steps:
        for m in s.media:
            assert (media_dir / m.path).exists(), m.path


def test_step_indices_must_be_contiguous():
    data = _base()
    data["steps"][1]["index"] = 5
    with pytest.raises(ValidationError):
        InstructionSet.model_validate(data)


def test_pick_step_without_position_and_place_with_position():
    with pytest.raises(ValidationError):
        Step(index=0, type="place", text="x", expected_time_s=1, part_id="p")
    with pytest.raises(ValidationError):
        Step(index=0, type="pick", text="x", expected_time_s=1, part_id="p", position_ids=["P1"])


def test_settings_cannot_hide_everything():
    InstructionSettings(hidden_elements=["image", "video"])        # allowed: text only
    with pytest.raises(ValidationError):
        InstructionSettings(hidden_elements=["text", "image", "video"])
    with pytest.raises(ValidationError):
        InstructionSettings(hidden_elements=["progress"])


def test_analysis_result_strict():
    ok = {
        "progress_assessment": "stabil", "plateau": True, "target_merge_level": 1,
        "participant_message": "Hallo", "researcher_reasoning": "r",
    }
    AnalysisResult.model_validate(ok)
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate({**ok, "extra_field": 1})
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate({**ok, "target_merge_level": 5})
