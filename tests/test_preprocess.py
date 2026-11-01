"""Per-step metrics and RunSummary on hand-built gaze sequences with known answers."""
import json

import pytest

from app.config import PROJECT_ROOT, load_config
from app.models import ALL_AOIS, GAZE_METRIC_FIELDS, GazeSample, InstructionSet, StepEvent
from app.pipeline.aoi import AoiMap
from app.pipeline.preprocess import PreprocessError, compute_step_metrics, step_intervals, visit_sequence
from app.pipeline.summary import SummaryHistory, build_run_summary, summary_for_condition

import numpy as np

W, H = 1920, 1080
DT = 0.1  # 10 Hz keeps hand counting easy

# A point inside each AOI (normalised), plus pseudo-AOIs
P = {
    "text": (960 / W, 200 / H),
    "image": (500 / W, 600 / H),
    "video": (1400 / W, 600 / H),
    "btn_next": (1700 / W, 980 / H),
    "other": (960 / W, 300 / H),
    "off_screen": (0.5, 1.4),
}


@pytest.fixture(scope="module")
def amap():
    return AoiMap.from_config(load_config())


@pytest.fixture(scope="module")
def iset():
    return InstructionSet.model_validate_json(
        (PROJECT_ROOT / "data/instruction_sets/duplo_tower_base.json").read_text(encoding="utf-8"))


def samples_from(t0: float, labels: list[str]) -> list[GazeSample]:
    """One sample per label, DT apart, starting at t0 + DT/2 (well inside the interval)."""
    out = []
    for i, lab in enumerate(labels):
        t = t0 + DT / 2 + i * DT
        if lab == "invalid":
            out.append(GazeSample(t=t, x_norm=None, y_norm=None, valid=False))
        else:
            x, y = P[lab]
            out.append(GazeSample(t=t, x_norm=x, y_norm=y, valid=True))
    return out


def ev(t, step, kind, rep=1):
    return StepEvent(t=t, run=rep, step_index=step, event=kind)


def simple_run(iset, per_step_labels):
    """Each step i lasts len(labels)*DT, consecutive, no back presses."""
    samples, events, t = [], [], 0.0
    for i, labels in enumerate(per_step_labels):
        events.append(ev(t, i, "start"))
        samples += samples_from(t, labels)
        t += len(labels) * DT
        events.append(ev(t, i, "end"))
    return samples, events


def filler(iset):
    return [["text"] * 5 for _ in range(iset.n_steps)]


# --- known-answer metrics ----------------------------------------------------------------------

def test_step_metrics_known_answer(iset, amap):
    steps = filler(iset)
    # step 0: 10 samples -> 1.0 s
    steps[0] = ["text", "text", "text", "image", "image", "invalid", "text", "text", "off_screen", "off_screen"]
    samples, events = simple_run(iset, steps)
    m = compute_step_metrics(iset, samples, events, amap)[0]

    assert m.step_index == 0
    assert m.duration_s == pytest.approx(1.0)
    assert m.duration_vs_expected_ratio == pytest.approx(1.0 / 1.5, abs=1e-3)
    assert m.dwell_pct["text"] == 50.0
    assert m.dwell_pct["image"] == 20.0
    assert m.dwell_pct["invalid"] == 10.0
    assert m.dwell_pct["off_screen"] == 20.0
    assert m.dwell_pct["video"] == 0.0
    assert set(m.dwell_pct) == set(ALL_AOIS)
    assert sum(m.dwell_pct.values()) == pytest.approx(100.0)
    # invalid sample is a gap: text,text,text,image,image,|,text,text,off,off
    assert m.transitions == ["text", "image", "text", "off_screen"]
    assert m.first_aoi == "text"
    assert m.revisit_count == 1
    assert m.n_valid_samples == 9
    assert m.back_presses == 0


def test_other_is_a_gap_not_a_visit(iset, amap):
    steps = filler(iset)
    steps[2] = ["other", "image", "other", "image", "video", "other"]
    samples, events = simple_run(iset, steps)
    m = compute_step_metrics(iset, samples, events, amap)[2]
    assert m.transitions == ["image", "video"]       # image-other-image collapses to one visit
    assert m.first_aoi == "image"
    assert m.revisit_count == 0
    assert m.dwell_pct["other"] == 50.0
    assert m.n_valid_samples == 6


def test_step_without_samples(iset, amap):
    steps = filler(iset)
    samples, events = simple_run(iset, steps)
    samples = [s for s in samples if not (0 <= s.t < 0.5)]  # drop all of step 0
    m = compute_step_metrics(iset, samples, events, amap)[0]
    assert m.n_valid_samples == 0 and m.first_aoi is None and m.transitions == []
    assert all(v == 0.0 for v in m.dwell_pct.values())
    assert m.duration_s == pytest.approx(0.5)


def test_back_press_intervals_and_attribution(iset, amap):
    # step0 [0,1) -> step1 [1,1.5) back -> step0 [1.5,2) -> step1 [2,3) -> rest
    events = [ev(0.0, 0, "start"), ev(1.0, 0, "end"),
              ev(1.0, 1, "start"), ev(1.5, 1, "back"),
              ev(1.5, 0, "start"), ev(2.0, 0, "end"),
              ev(2.0, 1, "start"), ev(3.0, 1, "end")]
    samples = (samples_from(0.0, ["text"] * 10) + samples_from(1.0, ["video"] * 5)
               + samples_from(1.5, ["image"] * 5) + samples_from(2.0, ["video"] * 10))
    t = 3.0
    for i in range(2, iset.n_steps):
        events += [ev(t, i, "start"), ev(t + 0.5, i, "end")]
        samples += samples_from(t, ["text"] * 5)
        t += 0.5
    m = compute_step_metrics(iset, samples, events, amap)
    assert m[0].duration_s == pytest.approx(1.5)
    assert m[1].duration_s == pytest.approx(1.5)
    assert m[0].back_presses == 0 and m[1].back_presses == 1
    # step 0 got its own samples from both intervals, none of step 1's
    assert m[0].dwell_pct["text"] == pytest.approx(100 * 10 / 15, abs=0.01)
    assert m[0].dwell_pct["image"] == pytest.approx(100 * 5 / 15, abs=0.01)
    assert m[0].dwell_pct["video"] == 0.0
    assert m[0].transitions == ["text", "image"]
    assert m[1].dwell_pct["video"] == 100.0
    assert m[1].transitions == ["video"]          # two intervals, same AOI -> collapsed


def test_sample_on_interval_boundary_goes_to_next_step(iset, amap):
    steps = filler(iset)
    samples, events = simple_run(iset, steps)
    x, y = P["video"]
    samples.append(GazeSample(t=0.5, x_norm=x, y_norm=y, valid=True))  # exactly end(0) == start(1)
    m = compute_step_metrics(iset, samples, events, amap)
    assert m[0].dwell_pct["video"] == 0.0
    assert m[1].dwell_pct["video"] > 0.0


def test_unsorted_samples_are_handled(iset, amap):
    steps = filler(iset)
    steps[0] = ["text", "image", "video", "text", "image"]
    samples, events = simple_run(iset, steps)
    a = compute_step_metrics(iset, samples, events, amap)
    b = compute_step_metrics(iset, list(reversed(samples)), events, amap)
    assert a == b


# --- min visit filter --------------------------------------------------------------------------

def test_min_visit_drops_short_blips():
    t = np.arange(8) * DT
    labels = np.array(["text", "text", "text", "image", "text", "text", "video", "video"], dtype=object)
    assert visit_sequence(t, labels, 0.0) == ["text", "image", "text", "video"]
    # image lasts 1 sample = 0.1 s -> dropped at 0.15 s; text runs then merge
    assert visit_sequence(t, labels, 0.15) == ["text", "video"]
    assert visit_sequence(t, labels, 0.1) == ["text", "image", "text", "video"]


# --- event validation ----------------------------------------------------------------------------

@pytest.mark.parametrize("events, msg", [
    ([ev(0, 0, "start")], "never closed"),
    ([ev(0, 0, "start"), ev(1, 1, "start")], "still open"),
    ([ev(0, 0, "end")], "open step is None"),
    ([ev(1, 0, "start"), ev(0.5, 0, "end")], "backwards"),
    ([ev(0, 0, "start"), ev(1, 0, "end")], "never completed"),
])
def test_bad_event_sequences_raise(iset, events, msg):
    with pytest.raises(PreprocessError, match=msg):
        step_intervals(events, iset.n_steps)


# --- RunSummary, history, condition views --------------------------------------------------------

def test_run_summary_totals(iset, amap):
    samples, events = simple_run(iset, filler(iset))
    s = build_run_summary(3, 3, iset, samples, events, amap)
    assert s.run == 3 and s.repetition == 3 and not s.is_trial and s.version_level == 0 and s.instruction_set_id == iset.id
    assert len(s.steps) == iset.n_steps
    assert s.totals.total_duration_s == pytest.approx(0.5 * iset.n_steps)
    assert s.totals.total_expected_s == pytest.approx(sum(st.expected_time_s for st in iset.steps))
    assert s.totals.total_back_presses == 0
    json.dumps(s.model_dump(mode="json"))  # serialisable


def _all_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _all_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _all_keys(v)


def test_time_only_strips_all_gaze_fields(iset, amap):
    samples, events = simple_run(iset, filler(iset))
    s = build_run_summary(1, 1, iset, samples, events, amap)
    gaze = summary_for_condition(s, "gaze")
    timing = summary_for_condition(s, "time_only")
    for f in GAZE_METRIC_FIELDS:
        assert f in gaze["steps"][0]
    keys = set(_all_keys(timing))
    assert not keys & set(GAZE_METRIC_FIELDS)
    assert not keys & set(ALL_AOIS)                      # no AOI names leaked as keys
    assert "aoi" not in json.dumps(timing).lower()
    assert timing["steps"][0]["duration_s"] == s.steps[0].duration_s
    assert timing["steps"][0]["back_presses"] == 0
    # the original summary is untouched
    assert s.steps[0].dwell_pct
    with pytest.raises(ValueError):
        summary_for_condition(s, "bogus")


def test_history(iset, amap):
    samples, events = simple_run(iset, filler(iset))
    h = SummaryHistory()
    for r in (1, 2, 3, 4):
        h.add(build_run_summary(r, r, iset, samples, events, amap))
    assert [s.run for s in h.last(2)] == [3, 4]
    assert [s.run for s in h.last(10)] == [1, 2, 3, 4]
    assert h.last(0) == []
    with pytest.raises(ValueError):
        h.add(build_run_summary(2, 2, iset, samples, events, amap))


def test_trial_run_has_no_repetition(iset, amap):
    samples, events = simple_run(iset, filler(iset))
    h = SummaryHistory()
    h.add(build_run_summary(1, 1, iset, samples, events, amap))
    trial = build_run_summary(2, None, iset, samples, events, amap)
    h.add(trial)
    h.add(build_run_summary(3, 2, iset, samples, events, amap))
    assert trial.is_trial and trial.repetition is None
    assert [s.repetition for s in h.regular()] == [1, 2]      # the trial never shifts the numbering
    assert h.get(2) is trial and h.get(99) is None
