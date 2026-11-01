"""LLM layer: participant request, plan validation (call 1), generation validation (call 2),
mock client, prompts (incl. time_only stripping), Ollama client against a fake local server."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.config import PROJECT_ROOT, load_config
from app.core.repository import ContentRepository
from app.llm.client import LLMError
from app.llm.mock_client import MockLLMClient, mock_analysis, mock_generation
from app.llm.prompts import PromptTemplates, build_analysis_prompt, build_generation_prompt
from app.llm.validate import (
    allowed_levels, check_request, fallback_plan, group_kind, requested_hidden, structure_key, validate_analysis,
    validate_generation,
)
from app.models import GAZE_METRIC_FIELDS, InstructionSet, InstructionSettings, ParticipantRequest
from app.pipeline.aoi import AoiMap
from app.pipeline.summary import build_run_summary
from tests.test_preprocess import DT, simple_run

BASE = InstructionSet.model_validate_json(
    (PROJECT_ROOT / "data/instruction_sets/duplo_tower_base.json").read_text(encoding="utf-8"))
CFG = load_config()
TEMPLATES = PromptTemplates(CFG.resolve(CFG.paths.prompts))
S0 = InstructionSettings()
S1 = InstructionSettings(merge_level=1)
S2 = InstructionSettings(merge_level=2)


def req(direction="compress", hide=(), show=(), difficulty="easy"):
    return ParticipantRequest(direction=direction, hide=list(hide), show=list(show), difficulty=difficulty)


def plan(**over):
    d = {"progress_assessment": "ok", "plateau": False, "target_merge_level": 1, "keep_detailed_steps": [],
         "participant_message": "Shorter steps.", "researcher_reasoning": "r"}
    d.update(over)
    return json.dumps(d)


# --- request ----------------------------------------------------------------------------------------

def test_request_model():
    r = req(hide=["video", "video"])
    assert r.hide == ["video"] and not r.is_empty
    assert ParticipantRequest(direction="keep", difficulty="hard").is_empty   # a rating alone changes nothing
    with pytest.raises(ValueError):
        req(hide=["video"], show=["video"])
    with pytest.raises(ValueError):
        ParticipantRequest(direction="sideways")
    with pytest.raises(ValueError):
        ParticipantRequest(direction="keep", difficulty="x" * 41)


def test_allowed_levels():
    assert allowed_levels(0, "compress") == [1, 2]
    assert allowed_levels(2, "compress") == []
    assert allowed_levels(2, "detail") == [0, 1]
    assert allowed_levels(1, "keep") == [1]


def test_requested_hidden_and_checks():
    cur = InstructionSettings(hidden_elements=["video"])
    assert requested_hidden(cur, req("keep", hide=["image"], show=["video"])) == ["image"]
    assert check_request(cur, req("keep", hide=["image", "text"]))       # would hide everything
    assert check_request(S2, req("compress"))                            # already most compact
    assert not check_request(S0, req("keep", hide=["gamification"]))      # gamification is not content


# --- call 1 ----------------------------------------------------------------------------------------

def test_valid_plan_passes():
    result, outcome = validate_analysis(plan(keep_detailed_steps=[3, 2, 3]), S0, req(), BASE.n_steps)
    assert outcome.ok and result.target_merge_level == 1
    assert result.keep_detailed_steps == [2, 3]


@pytest.mark.parametrize("raw", [None, "", "not json", "[]", plan(plateau="yes"), plan(target_merge_level=5),
                                 plan(extra_field=1), plan(keep_detailed_steps=[99]),
                                 plan(participant_message="x" * 601)])
def test_malformed_plan_rejected(raw):
    result, outcome = validate_analysis(raw, S0, req(), BASE.n_steps)
    assert result is None and not outcome.ok and outcome.errors


@pytest.mark.parametrize("current,direction,level", [(S0, "compress", 0), (S1, "detail", 1), (S1, "detail", 2),
                                                     (S1, "keep", 2)])
def test_plan_must_follow_requested_direction(current, direction, level):
    result, outcome = validate_analysis(plan(target_merge_level=level), current, req(direction), BASE.n_steps)
    assert result is None and "not allowed" in outcome.errors[0]


def test_empty_message_gets_default_and_code_fence_accepted():
    result, outcome = validate_analysis("```json\n" + plan(participant_message="  ") + "\n```", S0, req(),
                                        BASE.n_steps)
    assert outcome.ok and result.participant_message and outcome.warnings


def test_fallback_plan_is_smallest_step():
    assert fallback_plan(S0, req("compress"), "x").target_merge_level == 1
    assert fallback_plan(S2, req("detail"), "x").target_merge_level == 1
    assert fallback_plan(S1, req("keep"), "x").target_merge_level == 1


# --- call 2 ----------------------------------------------------------------------------------------

def gen_payload(level, keep=()):
    _, _, payload = build_generation_prompt(TEMPLATES, BASE, InstructionSettings(merge_level=level), list(keep),
                                            req(), 220)
    return payload


def validate(raw, level, keep=(), max_chars=220):
    return validate_generation(raw if isinstance(raw, str) else json.dumps(raw), BASE,
                               InstructionSettings(merge_level=level), list(keep), 1, max_chars)


@pytest.mark.parametrize("level", [1, 2])
def test_mock_generation_passes_validator(level):
    new, outcome = validate(mock_generation(gen_payload(level)), level)
    assert outcome.ok, outcome.errors
    assert [i for s in new.steps for i in s.covered()] == list(range(BASE.n_steps))
    assert new.n_steps < BASE.n_steps
    for s in new.steps:                                            # structure derived by the app
        assert s.type == group_kind(BASE, s.covers)
        assert s.expected_time_s == pytest.approx(sum(BASE.steps[i].expected_time_s for i in s.covers))
        assert s.position_ids == [p for i in s.covers for p in BASE.steps[i].position_ids]


def test_keep_detailed_steps_stay_separate():
    new, outcome = validate(mock_generation(gen_payload(1, keep=[2, 3])), 1, keep=[2, 3])
    assert outcome.ok
    assert [2] in [s.covers for s in new.steps] and [3] in [s.covers for s in new.steps]
    merged = {"covers": [2, 3], "text": "x", "media": []}
    raw = mock_generation(gen_payload(1))
    raw["steps"] = [s for s in raw["steps"] if not set(s["covers"]) & {2, 3}]
    raw["steps"].insert(1, merged)
    new, outcome = validate(raw, 1, keep=[2, 3])
    assert new is None and any("stay detailed" in e for e in outcome.errors)


def test_generation_rejects_dropped_reordered_and_illegal_groups():
    good = mock_generation(gen_payload(1))
    dropped = {"steps": good["steps"][1:]}
    assert validate(dropped, 1)[0] is None
    reordered = {"steps": [good["steps"][1], good["steps"][0], *good["steps"][2:]]}
    assert validate(reordered, 1)[0] is None
    place_pick = {"steps": [{"covers": [0], "text": "a", "media": []}, {"covers": [1, 2], "text": "b", "media": []},
                            *[{"covers": [i], "text": "c", "media": []} for i in range(3, BASE.n_steps)]]}
    new, outcome = validate(place_pick, 1)
    assert new is None and any("cannot be combined" in e for e in outcome.errors)


def test_level1_rejects_multi_pair_groups():
    level2 = mock_generation(gen_payload(2))
    if any(len(s["covers"]) > 2 for s in level2["steps"]):
        new, outcome = validate(level2, 1)
        assert new is None and any("merge level 2" in e for e in outcome.errors)


def test_generation_rejects_foreign_media_and_long_text():
    raw = mock_generation(gen_payload(1))
    raw["steps"][0]["media"] = ["../../etc/passwd"]
    assert validate(raw, 1)[0] is None
    raw = mock_generation(gen_payload(1))
    raw["steps"][0]["media"] = [BASE.steps[-1].media[0].path]          # media of another step
    assert validate(raw, 1)[0] is None
    raw = mock_generation(gen_payload(1))
    raw["steps"][0]["text"] = "x" * 300
    new, outcome = validate(raw, 1, max_chars=220)
    assert new is None and any("longer than" in e for e in outcome.errors)


@pytest.mark.parametrize("raw", [None, "", "{}", '{"steps": "broken"}', '{"steps": []}',
                                 '{"steps": [{"covers": [0], "text": "a", "media": [], "type": "pick"}]}'])
def test_generation_malformed(raw):
    new, outcome = validate_generation(raw, BASE, S1, [], 1, 220)
    assert new is None and not outcome.ok


def test_structure_key_detects_identical_versions():
    a, _ = validate(mock_generation(gen_payload(1)), 1)
    b, _ = validate(mock_generation(gen_payload(1)), 1)
    c, _ = validate(mock_generation(gen_payload(2)), 2)
    assert structure_key(a) == structure_key(b) != structure_key(c)


# --- mock plan + prompts ----------------------------------------------------------------------------

def _summaries(durations, trial_last=False):
    amap = AoiMap.from_config(CFG)
    out = []
    for i, d in enumerate(durations, start=1):
        per_step = max(1, round(d / (BASE.n_steps * DT)))
        samples, events = simple_run(BASE, [["text"] * per_step for _ in range(BASE.n_steps)])
        trial = trial_last and i == len(durations)
        out.append(build_run_summary(i, None if trial else i, BASE, samples, events, amap))
    return out


def _analysis_payload(summaries, request, condition="gaze", current=BASE):
    _, _, payload = build_analysis_prompt(TEMPLATES, "rules", condition, BASE, current, summaries, request, [])
    return payload


def test_mock_plan_levels_and_plateau():
    assert mock_analysis(_analysis_payload(_summaries([20, 10]), req()))["target_merge_level"] == 1
    assert mock_analysis(_analysis_payload(_summaries([10, 10]), req()))["target_merge_level"] == 2
    keep = mock_analysis(_analysis_payload(_summaries([10]), req("keep")))
    assert keep["target_merge_level"] == 0 and keep["keep_detailed_steps"] == []


def test_mock_client_output_validates():
    client = MockLLMClient()
    payload = _analysis_payload(_summaries([12, 11]), req())
    result, outcome = validate_analysis(client.complete_json("analysis", "", {}, payload), S0, req(), BASE.n_steps)
    assert outcome.ok and result.participant_message


def test_analysis_prompt_contains_request_rules_and_allowed_levels():
    prompt, schema, payload = build_analysis_prompt(TEMPLATES, "RULE-XYZ", "gaze", BASE, BASE, _summaries([10]),
                                                    req(hide=["video"], difficulty="hard"), [])
    assert "RULE-XYZ" in prompt and "hard" in prompt and "video" in prompt
    assert payload["allowed_target_merge_levels"] == [1, 2]
    assert payload["resulting_hidden_elements"] == ["video"]
    assert "target_merge_level" in json.dumps(schema)
    assert "$payload" not in prompt and "$rules" not in prompt          # templates fully substituted


def test_time_only_prompt_contains_no_gaze_data():
    prompt, _, payload = build_analysis_prompt(TEMPLATES, "r", "time_only", BASE, BASE, _summaries([10, 9]),
                                               req(), [])
    dumped = json.dumps(payload["run_summaries"])
    for f in GAZE_METRIC_FIELDS:
        assert f'"{f}"' not in dumped
    assert '"dwell_pct"' not in prompt


def test_generation_prompt_includes_media_descriptions():
    repo = ContentRepository(CFG)
    desc = {p: i.description for p, i in repo.catalog().by_path().items()}
    _, _, payload = build_generation_prompt(TEMPLATES, BASE, S1, [], req(), 220, desc)
    described = [m for s in payload["base_steps"] for m in s["media"] if m["description"]]
    assert described, "catalog descriptions reach the LLM"


# --- Ollama client against a fake local server -------------------------------------------------------

class _FakeOllama(BaseHTTPRequestHandler):
    reply = {"message": {"content": "{}"}}
    seen: list = []

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _FakeOllama.seen.append(body)
        data = json.dumps(_FakeOllama.reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture
def fake_ollama():
    srv = HTTPServer(("127.0.0.1", 0), _FakeOllama)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def test_ollama_client_sends_schema_and_returns_content(fake_ollama):
    from app.llm.ollama_client import OllamaClient
    cfg = CFG.llm.ollama.model_copy(update={"host": "127.0.0.1", "port": fake_ollama.server_port})
    _FakeOllama.reply = {"message": {"content": plan()}}
    _FakeOllama.seen.clear()
    raw = OllamaClient(cfg).complete_json("analysis", "PROMPT", {"type": "object"}, {})
    assert json.loads(raw)["target_merge_level"] == 1
    sent = _FakeOllama.seen[-1]
    assert sent["format"] == {"type": "object"} and sent["stream"] is False
    assert "PROMPT" in json.dumps(sent["messages"])


def test_ollama_client_errors_are_llm_errors(fake_ollama):
    from app.llm.ollama_client import OllamaClient
    cfg = CFG.llm.ollama.model_copy(update={"host": "127.0.0.1", "port": fake_ollama.server_port})
    _FakeOllama.reply = {"unexpected": True}
    with pytest.raises(LLMError):
        OllamaClient(cfg).complete_json("analysis", "p", {}, {})
    dead = CFG.llm.ollama.model_copy(update={"host": "127.0.0.1", "port": 9, "timeout_s": 2})
    with pytest.raises(LLMError):
        OllamaClient(dead).complete_json("analysis", "p", {}, {})
