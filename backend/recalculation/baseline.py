# Ответственный: Человек 2 (recalculation + risk)
"""Фиксация неизменяемого baseline-плана на Неделе 0."""

from __future__ import annotations

from typing import Any


def save_baseline(plan: Any) -> None:
    """Сохраняет снимок плана на Неделе 0 как baseline.

    ЭТА ФУНКЦИЯ НЕ ДОЛЖНА ВЫЗЫВАТЬСЯ ПОВТОРНО В ТЕЧЕНИЕ КВАРТАЛА.
    baseline фиксируется один раз и далее доступен только на чтение
    (см. contracts/plan_schema.md, раздел "Правило неизменности baseline").

    Args:
        plan: Plan с уже построенным current-планом (результат
            planning/scheduler.py::build_baseline_plan), который нужно
            зафиксировать как plan.baseline.
    """
    raise NotImplementedError
