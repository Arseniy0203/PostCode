# Ответственный: Человек 2 (recalculation + risk)
"""Календарь квартала и сроки кадровых решений."""

from __future__ import annotations

from datetime import date, timedelta

from backend.explainability.reasons import QUARTER_SPRINTS

# Ответ экспертов: квартал календарный, старт 1 июля.
QUARTER_START = date(2026, 7, 1)
SPRINT_DAYS = 14

# Допущения (менять здесь): сколько спринтов проходит от решения до того,
# как человек реально работает над задачей.
HIRE_LEAD_SPRINTS = 3     # поиск и выход нового сотрудника ≈ 6 недель
BORROW_LEAD_SPRINTS = 1   # передача дел и переключение из другой команды ≈ 2 недели


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


def next_quarter_start(start: date = QUARTER_START) -> date:
    month = start.month + 3
    return date(start.year + (month - 1) // 12, (month - 1) % 12 + 1, 1)


def ready_sprint(now: int, lead_sprints: int) -> int:
    """Первый спринт, в котором человек работает, если решение принято в спринте now."""
    return now + lead_sprints + 1
