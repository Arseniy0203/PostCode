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

# Продуктивность по спринтам с момента выхода; дальше — 100 %.
ONBOARDING_RAMP = (0.5, 0.75)   # новый сотрудник: адаптация, доступы, вход в код
BORROW_RAMP = (0.75,)           # переключённый из другой команды: вход в контекст


def ramp_factor(sprints_since_join: int, ramp: tuple[float, ...]) -> float:
    return ramp[sprints_since_join] if 0 <= sprints_since_join < len(ramp) else 1.0


def average_ramp(joined: int, first: int, last: int = QUARTER_SPRINTS,
                 ramp: tuple[float, ...] = ONBOARDING_RAMP) -> float:
    """Средняя продуктивность человека, вышедшего в спринте joined, на спринтах first…last.

    Планировщик принимает одну ёмкость инженера на все спринты, поэтому новичку
    даётся средняя по оставшемуся горизонту: суммарные часы считаются точно,
    а следующий пересчёт уточняет распределение.
    """
    sprints = range(max(first, joined), last + 1)
    if not sprints:
        return 1.0
    total = sum(ramp_factor(s - joined, ramp) for s in sprints)
    return total / (last - first + 1) if last >= first else 1.0


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
