"""Participant-facing description of a layout change (texts from data/locale)."""
from __future__ import annotations

from app.i18n import t
from app.models import HIDEABLE_ELEMENTS, InstructionSettings


def describe_change(current: InstructionSettings, target: InstructionSettings) -> list[str]:
    items: list[str] = []
    a, b = current.merge_level, target.merge_level
    if b > a:
        items += [t(f"changes.merge_up.{lv}") for lv in range(a + 1, b + 1)]
    elif b < a:
        items += [t(f"changes.merge_down.{lv}") for lv in range(a - 1, b - 1, -1)]
    cur, new = set(current.hidden_elements), set(target.hidden_elements)
    items += [t(f"changes.hide.{el}") for el in HIDEABLE_ELEMENTS if el in new - cur]
    items += [t(f"changes.show.{el}") for el in HIDEABLE_ELEMENTS if el in cur - new]
    return items
