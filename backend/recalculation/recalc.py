# Ответственный: Человек 2 (recalculation + risk)
"""Пересчёт плана по факту выполнения спринта."""

from __future__ import annotations

from typing import Any


def apply_sprint_fact(current_plan: Any, fact_data: dict[str, Any]) -> Any:
    """Обновляет current-план по факту прошедшего спринта.

    Затрагивает только незавершённый хвост плана (задачи в статусе
    Todo/InProgress). Задачи в статусе Done не пересчитываются, plan.baseline
    не изменяется (см. contracts/plan_schema.md).

    Важно разделять:
        - forecast_end_date — расчётная (прогнозная) дата/спринт окончания,
          пересчитывается каждый раз при вызове этой функции;
        - actual_end_date — дата/спринт фактического окончания,
          заполняется только когда задача реально переходит в Done.

    Args:
        current_plan: текущий Plan.current (список PlannedTask) до обновления.
        fact_data: факт по прошедшему спринту (Spent_time_roles + статусы задач).

    Returns:
        Обновлённый Plan с новым current (baseline не тронут).
    """
    raise NotImplementedError
