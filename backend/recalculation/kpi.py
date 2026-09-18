# Ответственный: Человек 2 (recalculation + risk)
"""Расчёт KPI квартального плана."""

from __future__ import annotations

from typing import Any


def pi_predictability(baseline: Any, current: Any) -> float:
    """Считает предсказуемость PI (Program Increment).

    Формула: (количество завершённых PRODF-целей за 12 недель) /
             (количество PRODF-целей, запланированных на Неделе 0, т.е. в baseline)
             x 100%.

    Args:
        baseline: неизменяемый список PlannedTask на Неделю 0 (Plan.baseline).
        current: актуальный список PlannedTask (Plan.current).

    Returns:
        Значение предсказуемости PI в процентах.
    """
    raise NotImplementedError


def say_do_ratio(baseline: Any, current: Any, sprint_n: int) -> float:
    """Считает Say-Do Ratio для конкретного спринта.

    Формула: (фактически выполненные SP в спринте sprint_n, из current) /
             (изначально запланированные SP на этот спринт, ИЗ BASELINE,
              а не из current) x 100%.

    Args:
        baseline: неизменяемый список PlannedTask на Неделю 0 (Plan.baseline).
        current: актуальный список PlannedTask (Plan.current), источник факта.
        sprint_n: номер спринта, для которого считается коэффициент.

    Returns:
        Значение Say-Do Ratio в процентах.
    """
    raise NotImplementedError
