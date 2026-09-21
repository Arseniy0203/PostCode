# Ответственный: Человек 2 (recalculation + risk)
"""Фиксация неизменяемого baseline-плана на Неделе 0."""

from __future__ import annotations

import copy
from typing import Any


class BaselineAlreadySavedError(RuntimeError):
    """baseline фиксируется один раз за квартал."""


def save_baseline(plan: dict[str, Any]) -> dict[str, Any]:
    """Фиксирует текущий план как baseline Недели 0.

    baseline и current становятся независимыми глубокими копиями: планировщик
    может вернуть в них один и тот же список, и тогда пересчёт current молча
    портил бы baseline. Повторный вызов запрещён (contracts/plan_schema.md).

    Args:
        plan: dict с ключом "current" (результат build_baseline_plan).

    Returns:
        Тот же plan с заполненными baseline, history и current_sprint = 0.
    """
    if plan.get("baseline_locked"):
        raise BaselineAlreadySavedError("baseline уже зафиксирован на Неделе 0")

    current = copy.deepcopy(list(plan.get("current") or plan.get("baseline") or []))
    plan["baseline"] = tuple(copy.deepcopy(current))
    plan["current"] = current
    plan["current_sprint"] = 0
    plan["history"] = [{
        "sprint": 0,
        "fact": None,
        "current": copy.deepcopy(current),
        "changes": [],
        "alerts": [],
        "warnings": [],
    }]
    plan["baseline_locked"] = True
    return plan
