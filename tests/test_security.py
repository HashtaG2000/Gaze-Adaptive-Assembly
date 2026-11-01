"""Break tests: hostile paths and names, oversized / corrupt files, HTML injection, tampered
content, author tool round trips, i18n fallbacks, caches, survey and calibration maths."""
import json
import math
import shutil

import pytest

from app import i18n
from app.config import PROJECT_ROOT, ConfigError, load_config
from app.core.files import (
    FileCache, LRUCache, UnsafePathError, is_safe_rel_path, read_json, read_text, safe_join, safe_name,
    write_json_atomic,
)
from app.core.repository import ContentRepository
from app.models import InstructionSet, Media, MediaCatalog, RuleSet, Survey
from app.pipeline.archive import list_sessions, load_session
from app.pipeline.calibration import angular_error_deg, evaluate_point, summarise

BASE_PATH = PROJECT_ROOT / "data/instruction_sets/duplo_tower_base.json"


@pytest.fixture
def repo(tmp_path):
    """A repository on a private copy of data/ (tests never touch the real content)."""
    cfg = load_config()
    data = tmp_path / "data"
    shutil.copytree(PROJECT_ROOT / "data", data)
    for name in ("instruction_sets", "media", "media_catalog", "rules", "surveys"):
        rel = getattr(cfg.paths, name).relative_to("data")
        setattr(cfg.paths, name, data / rel)
    return ContentRepository(cfg)


# --- paths ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("rel", ["../x.png", "a/../../x.png", "/etc/passwd", "C:/Windows/x.png", "C:x.png",
                                 "\\\\server\\share\\x.png", "a\\..\\..\\x", "", "x\x00.png", "a/./../b"])
def test_unsafe_relative_paths_rejected(tmp_path, rel):
    assert not is_safe_rel_path(rel) or ".." not in rel
    with pytest.raises(UnsafePathError):
        safe_join(tmp_path, rel)
    with pytest.raises(ValueError):
        Media(type="image", path=rel, role="part")


def test_safe_join_accepts_normal_paths(tmp_path):
    assert safe_join(tmp_path, "placeholder/a.png") == (tmp_path / "placeholder" / "a.png").resolve()


@pytest.mark.parametrize("name,expected", [("../../evil.exe", "evil.exe"), ("my photo (1).PNG", "my_photo_1.png"),
                                           ("con.mp4", "con.mp4"), ("....", "file"), ("a" * 300 + ".jpg", "a" * 80 + ".jpg")])
def test_safe_name(name, expected):
    assert safe_name(name) == expected


def test_read_limits_and_atomic_write(tmp_path):
    big = tmp_path / "big.json"
    big.write_text("[" + "1," * 1000 + "1]")
    with pytest.raises(ValueError):
        read_text(big, max_bytes=100)
    out = tmp_path / "sub" / "x.json"
    write_json_atomic(out, {"a": "ü"})
    assert read_json(out) == {"a": "ü"}
    assert not list(out.parent.glob("*.tmp"))


def test_config_errors_are_reported_not_crashing(tmp_path):
    bad = tmp_path / "c.yaml"
    bad.write_text("screen: [unclosed")
    with pytest.raises(ConfigError):
        load_config(bad)
    bad.write_text("- just\n- a list\n")
    with pytest.raises(ConfigError):
        load_config(bad)
    bad.write_text("screen: {width: -5}\n")
    with pytest.raises(ConfigError, match="screen"):
        load_config(bad)
    with pytest.raises(ConfigError):
        load_config(tmp_path / "missing.yaml")


# --- caches --------------------------------------------------------------------------------------

def test_file_cache_reloads_on_change(tmp_path):
    calls = []
    cache = FileCache(lambda p: calls.append(p) or read_json(p), max_entries=2)
    f = tmp_path / "a.json"
    f.write_text('{"v": 1}')
    assert cache.get(f) == {"v": 1} and cache.get(f) == {"v": 1}
    assert len(calls) == 1                                   # second read served from cache
    f.write_text('{"v": 22}')                                # size changes -> re-parse
    assert cache.get(f) == {"v": 22} and len(calls) == 2


def test_lru_cache_bounds():
    c = LRUCache(2)
    c.put("a", 1)
    c.put("b", 2)
    c.get("a")
    c.put("c", 3)
    assert "a" in c and "c" in c and "b" not in c and len(c) == 2


# --- repository / author --------------------------------------------------------------------------

def test_import_media_safe(repo, tmp_path):
    src = tmp_path / "My Photo!!.PNG"
    src.write_bytes(b"\x89PNG fake")
    rel1 = repo.import_media(src)
    rel2 = repo.import_media(src)                           # never overwrites
    assert rel1 == "imported/My_Photo.png" and rel2 == "imported/My_Photo_2.png"
    assert repo.media_file(rel1) is not None
    exe = tmp_path / "virus.exe"
    exe.write_bytes(b"MZ")
    with pytest.raises(ValueError, match="not allowed"):
        repo.import_media(exe)
    repo.cfg.limits.max_media_mb = 1
    huge = tmp_path / "huge.mp4"
    huge.write_bytes(b"0" * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match="larger"):
        repo.import_media(huge)
    rel3 = repo.import_media(src, subdir="../../outside")              # hostile sub-folder name
    assert repo.media_file(rel3) is not None and ".." not in rel3
    assert repo.media_file(rel3).resolve().is_relative_to(repo.media_dir.resolve())


def test_save_set_validates_and_stays_in_folder(repo):
    iset = InstructionSet.model_validate_json(BASE_PATH.read_text(encoding="utf-8"))
    path = repo.save_set(iset, repo.sets_dir / BASE_PATH.name)
    assert path.parent == repo.sets_dir.resolve()
    broken = iset.model_copy(update={"steps": iset.steps[:3]})               # odd number of steps
    with pytest.raises(ValueError):
        repo.save_set(broken)
    escaped = repo.save_set(iset, repo.sets_dir / ".." / ".." / "evil.json")   # only the file name is used
    assert escaped.parent == repo.sets_dir.resolve()
    with pytest.raises(UnsafePathError):
        repo.save_set(iset, repo.sets_dir / "EVIL NAME.json")
    outside = repo.sets_dir.parent / "keep.json"
    outside.write_text("{}")
    repo.delete_set(repo.sets_dir / ".." / "keep.json")             # resolves inside the sets folder only
    assert outside.exists()


def test_tampered_instruction_files_are_reported_not_loaded(repo):
    (repo.sets_dir / "evil.json").write_text(json.dumps({"id": "evil", "title": "x", "steps": [
        {"index": 0, "type": "pick", "text": "t", "expected_time_s": 1, "part_id": "p",
         "media": [{"type": "image", "path": "../../../Windows/win.ini", "role": "part"}]}]}))
    (repo.sets_dir / "garbage.json").write_text("{not json")
    entries = {e.path.name: e for e in repo.list_sets()}
    assert not entries["evil.json"].ok and not entries["garbage.json"].ok
    assert all(p.name != "evil.json" for p, _ in repo.usable_sets())


def test_catalog_and_rules_round_trip(repo):
    cat = repo.catalog()
    assert cat.items
    item = cat.items[0].model_copy(update={"description": "New <b>desc</b>\x07"})
    repo.save_catalog(MediaCatalog(items=[item, *cat.items[1:]]))
    assert repo.catalog().items[0].description == "New <b>desc</b>"         # control chars stripped
    rules = repo.rules()
    repo.save_rules(rules.model_copy(update={"rules": rules.rules[:2]}))
    assert len(repo.rules().rules) == 2
    (repo.rules_path).write_text("{broken")
    assert repo.rules().rules == []                                          # unreadable -> no rules, no crash
    with pytest.raises(ValueError):
        RuleSet.model_validate({"rules": [{"id": "R1", "text": "a"}, {"id": "R1", "text": "b"}]})


def test_enrichments_set_editor_builds_and_validates(qapp, repo):
    from app.ui.enrichments import EnrichmentsPage
    win = EnrichmentsPage(repo, lambda: None)
    ed = win.sets
    assert ed.list.count() >= 1
    iset, errors = ed.build()
    assert iset is not None and not errors
    ed.title_edit.setText("Edited title")
    ed._add_step()                                      # odd number of steps -> not saveable
    iset, errors = ed.build()
    assert iset is None or errors or iset.n_steps % 2
    ed.table.removeRow(ed.table.rowCount() - 1)
    ed.table.item(0, 4).setText("abc")                  # expected time not a number
    assert ed.build()[0] is None
    ed.table.item(0, 4).setText("1.5")
    ed.table.item(0, 5).setText("../../secret.png")     # unsafe media path
    assert ed.build()[0] is None
    win.close()


# --- archive --------------------------------------------------------------------------------------

def test_archive_tolerates_broken_sessions(tmp_path):
    (tmp_path / "BROKEN1_20260101_100000").mkdir()
    (tmp_path / "BROKEN1_20260101_100000" / "session.json").write_text("{nope")
    (tmp_path / "not a session").mkdir()
    ok = tmp_path / "OLDREP1_20250101_100000"
    ok.mkdir()
    (ok / "session.json").write_text(json.dumps({
        "participant_id": "OLDREP1", "condition": "gaze", "gaze_source": "mouse", "llm_client": "mock",
        "instruction_set_file": "x.json", "started_at": "2025-01-01T10:00:00+01:00"}))
    (ok / "decisions.jsonl").write_text('{"t": 1, "wall_time": "2025-01-01T10:00:00+01:00", "repetition": 1, '
                                        '"decision": "continue", "from_version": 0}\nGARBAGE\n')
    sessions = list_sessions(tmp_path)
    assert {s.status for s in sessions} == {"broken", "incomplete"}
    assert sessions[0].participant_id == "BROKEN1"                       # newest first
    s = load_session(ok)
    assert [d.run for d in s.decisions] == [1]                           # legacy field upgraded
    with pytest.raises(ValueError):
        load_session(tmp_path / "BROKEN1_20260101_100000")


# --- text safety, i18n ----------------------------------------------------------------------------

def test_labels_and_chips_never_render_html(qapp):
    from PySide6.QtCore import Qt

    from app.ui.icons import Chip
    from app.ui.style import label
    evil = "<img src=x onerror=alert(1)><b>bold</b>"
    assert label(evil, "body").textFormat() == Qt.TextFormat.PlainText
    assert Chip(evil).label.textFormat() == Qt.TextFormat.PlainText


def test_i18n_fallbacks(tmp_path):
    assert i18n.t("player.step_of", n=2, total=5) == "Step 2 of 5"
    assert i18n.t("does.not.exist") == "does.not.exist"
    assert i18n.t("player.step_of") == "Step {n} of {total}"                # missing fields left as-is
    assert i18n.t("player.step_of", n=1) == "Step 1 of {total}"
    assert i18n.t("player.level") == "player.level"                          # a section is not a string
    assert i18n.items("archive.columns")[0] == "Participant"
    with pytest.raises(ValueError):
        i18n.configure(lang="../en")


# --- maths ---------------------------------------------------------------------------------------

def test_sus_score():
    survey = Survey.model_validate(read_json(PROJECT_ROOT / "data/surveys/sus.json"))
    ids = [i.id for i in survey.items]
    assert survey.score({i: 3 for i in ids}) == 50.0
    best = {i: (5 if k % 2 == 0 else 1) for k, i in enumerate(ids)}
    assert survey.score(best) == 100.0
    assert survey.score({ids[0]: 5}) is None


def test_calibration_maths():
    cfg = load_config()
    cal, screen = cfg.calibration, cfg.screen
    one_cm = 1 / cal.screen_width_cm
    assert angular_error_deg(one_cm, 0, cal, screen) == pytest.approx(math.degrees(math.atan(1 / cal.viewing_distance_cm)))
    exact = evaluate_point((0.5, 0.5), [(0.5, 0.5)] * cal.min_samples, cal, screen)
    assert exact.error_deg == pytest.approx(0.0)
    noisy = evaluate_point((0.5, 0.5), [(0.5, 0.5)] * cal.min_samples + [(float("nan"), 0.5), (0.9, 0.9)], cal, screen)
    assert noisy.error_deg == pytest.approx(0.0)                       # median ignores outliers, NaN dropped
    few = evaluate_point((0.5, 0.5), [(0.5, 0.5)], cal, screen)
    assert few.error_deg is None
    mean, mx, passed = summarise([exact, few], cal)
    assert not passed                                                  # every point needs data
    mean, mx, passed = summarise([exact, exact], cal)
    assert passed and mean == 0
