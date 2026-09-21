# Ответственный: Человек 2 (recalculation + risk)
"""Календарь квартала: даты спринтов для человекочитаемых текстов."""

from __future__ import annotations

from datetime import date, timedelta

from backend.explainability.reasons import QUARTER_SPRINTS

# Ответ экспертов: квартал календарный, старт 1 июля.
QUARTER_START = date(2026, 7, 1)
SPRINT_DAYS = 14


def sprint_dates(n: int, start: date = QUARTER_START) -> tuple[date, date]:
    first = start + timedelta(days=(n - 1) * SPRINT_DAYS)
    return first, first + timedelta(days=SPRINT_DAYS - 1)


def sprint_label(n: int | None, start: date = QUARTER_START) -> str:
    if n is None:
        return "срок не определён"
    if n < 1:
        return "до начала квартала"
    first, last = sprint_dates(n, start)
    suffix = "" if n <= QUARTER_SPRINTS else ", за пределами квартала"
    return f"спринт {n} ({first:%d.%m}–{last:%d.%m}{suffix})"
