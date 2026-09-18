# Ответственный: Человек 3 (starmap + explainability)
"""Расчёт Bus Factor компетенций и связи с рисками текущего плана."""

from __future__ import annotations

from typing import Any


def bus_factor(competency: str, engineers: list[dict[str, Any]]) -> int:
    """Считает Bus Factor компетенции.

    Bus Factor = число инженеров, у которых данная компетенция присутствует
    в skills_declared.

    Args:
        competency: название компетенции.
        engineers: список профилей инженеров (Engineers_profiles).

    Returns:
        Число инженеров-носителей компетенции.
    """
    raise NotImplementedError


def find_single_points_of_failure(engineers: list[dict[str, Any]]) -> list[str]:
    """Находит все компетенции с Bus Factor = 1.

    Args:
        engineers: список профилей инженеров (Engineers_profiles).

    Returns:
        Список названий компетенций, которыми владеет ровно один инженер.
    """
    raise NotImplementedError


def link_risk_to_tasks(plan: Any, competencies: list[str]) -> dict[str, Any]:
    """Связывает компетенции-одиночки (Bus Factor = 1) с задачами текущего плана.

    Ключевая связка между звёздной картой компетенций и планом: показывает,
    какие задачи текущего плана зависят от единственного носителя компетенции
    и, соответственно, находятся под риском при его недоступности.

    Args:
        plan: текущий Plan (используется Plan.current).
        competencies: список компетенций с Bus Factor = 1 (результат
            find_single_points_of_failure).

    Returns:
        Словарь competency -> список task_id, зависящих от этой компетенции.
    """
    raise NotImplementedError
