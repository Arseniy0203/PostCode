# Ответственный: Человек 4 (frontend + tests + docs)
"""Карточка задачи — переиспользуемый компонент (напр. в plan_view)."""

from __future__ import annotations

from typing import Any


def render_task_card(task: Any) -> None:
    """Отрисовывает карточку задачи.

    Отображает: SP, ЧЧ по ролям (estimated_hh), зависимости
    (Task_dependencies), причину решения текстом (decision + reason_code +
    reason_detail, см. contracts/task_schema.md::PlannedTask).

    Args:
        task: PlannedTask (см. contracts/task_schema.md).
    """
    raise NotImplementedError
