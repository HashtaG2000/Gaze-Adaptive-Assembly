"""AOI name constants shared by config, preprocessing and models."""
from typing import Literal

# Screen elements with fixed rectangles (from config.yaml).
ELEMENT_AOIS: tuple[str, ...] = (
    "progress", "text", "image", "video", "interaction", "btn_back", "btn_next",
)
# Pseudo-AOIs used in metrics.
# off_screen: valid sample outside the screen.
# other: valid sample on screen but in no element rectangle (PROVISIONAL, confirm with researcher).
# invalid: tracker reported the sample as invalid.
PSEUDO_AOIS: tuple[str, ...] = ("off_screen", "other", "invalid")
ALL_AOIS: tuple[str, ...] = ELEMENT_AOIS + PSEUDO_AOIS

# Elements the participant can remove / show again.
# "gamification" = avatar with thought bubble (inside the text AOI) and the streak / level badges
# (inside the progress AOI). It is not an AOI of its own, so AOIs stay comparable.
HideableElement = Literal["text", "image", "video", "gamification"]
HIDEABLE_ELEMENTS: tuple[str, ...] = ("text", "image", "video", "gamification")
# Content elements: at least one of them must always stay visible.
CONTENT_ELEMENTS: tuple[str, ...] = ("text", "image", "video")
MEDIA_ELEMENTS: tuple[str, ...] = ("image", "video")
