# Ответственный: Человек 4 (frontend + tests + docs)
"""Страница плана по спринтам (гант/таймлайн)."""

from __future__ import annotations

from typing import Any


def render_plan_view(plan: Any) -> None:
    """Отрисовывает план по спринтам в виде гант-диаграммы/таймлайна.

    Задачи окрашиваются по статус-цветам (Todo/InProgress/Done, а также
    postponed/cancelled). По клику на задачу отображается причина решения
    (см. frontend/views/task_card.py::render_task_card).

    Args:
        plan: Plan (baseline + current), см. contracts/plan_schema.md.
    """
    raise NotImplementedError
