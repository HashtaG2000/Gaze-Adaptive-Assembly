"""Point -> AOI mapping, incl. edges and off-screen."""
import numpy as np
import pytest
from pydantic import ValidationError

from app.config import AppConfig, load_config
from app.models import ELEMENT_AOIS
from app.pipeline.aoi import AoiMap

W, H = 1920, 1080


@pytest.fixture(scope="module")
def cfg():
    return load_config()


@pytest.fixture(scope="module")
def amap(cfg):
    return AoiMap.from_config(cfg)


def px(x, y):
    return x / W, y / H


def test_centre_of_every_element(cfg, amap):
    for name in ELEMENT_AOIS:
        r = cfg.aoi[name]
        assert amap.map_point(*px(r.x + r.w / 2, r.y + r.h / 2)) == name


def test_left_top_edge_inside_right_bottom_edge_outside(cfg, amap):
    r = cfg.aoi["image"]  # x 60..940, y 310..870
    assert amap.map_point(*px(r.x, r.y)) == "image"
    assert amap.map_point(*px(r.x + r.w - 0.01, r.y + r.h - 0.01)) == "image"
    assert amap.map_point(*px(r.x + r.w, r.y + 10)) == "other"   # gap between image and video
    assert amap.map_point(*px(r.x + 10, r.y + r.h)) == "other"   # gap below image
    assert amap.map_point(*px(r.x - 0.01, r.y + 10)) == "other"


def test_gap_between_aois_is_other(amap):
    assert amap.map_point(*px(960, 300)) == "other"   # between text and image/video
    assert amap.map_point(*px(10, 10)) == "other"     # screen margin


def test_screen_bounds(amap):
    for x, y in [(0.0, 0.0), (1.0, 1.0), (0.0, 1.0), (1.0, 0.0)]:
        assert amap.map_point(x, y) == "other"        # corners are on screen
    for x, y in [(-0.001, 0.5), (1.001, 0.5), (0.5, -0.001), (0.5, 1.001), (-3, 7)]:
        assert amap.map_point(x, y) == "off_screen"


def test_invalid_wins(amap):
    cx, cy = px(400, 600)  # inside image
    assert amap.map_point(cx, cy, valid=False) == "invalid"
    assert amap.map_point(None, cy) == "invalid"
    assert amap.map_point(float("nan"), cy) == "invalid"
    assert amap.map_point(5.0, 5.0, valid=False) == "invalid"


def test_vectorised_matches_scalar(amap):
    rng = np.random.default_rng(0)
    n = 5000
    x = rng.uniform(-0.2, 1.2, n)
    y = rng.uniform(-0.2, 1.2, n)
    valid = rng.random(n) > 0.1
    x[::97] = np.nan
    vec = amap.map_arrays(x, y, valid)
    scal = [amap.map_point(None if np.isnan(a) else a, b, v) for a, b, v in zip(x, y, valid)]
    assert list(vec) == scal
    assert set(vec) >= {"off_screen", "other", "invalid", "image", "video", "text"}


def test_config_rejects_overlapping_aois(cfg):
    raw = cfg.model_dump(mode="json")
    raw["aoi"]["video"]["x"] = 900  # now overlaps image (60..940)
    with pytest.raises(ValidationError, match="overlap"):
        AppConfig.model_validate(raw)
