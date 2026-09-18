# Ответственный: Человек 1 (ingestion + planning)
"""Расчёт доступной ёмкости команд и инженеров на спринт."""

from __future__ import annotations

from typing import Any


def team_capacity_sp(team_id: str, team_history: list[dict[str, Any]]) -> float:
    """Считает доступную ёмкость команды в Story Points на спринт.

    Формула: средний исторический Velocity команды (по Team_history)
    умноженный на Focus Factor = 0.8.

    Args:
        team_id: идентификатор команды.
        team_history: список исторических срезов (Team_history), см.
            backend/ingestion/models.py::TeamHistorySnapshot.

    Returns:
        Доступная ёмкость команды в SP на один спринт.
    """
    raise NotImplementedError


def engineer_capacity_hh(
    engineer_id: str,
    sprint: int,
    engineers: list[dict[str, Any]],
) -> float:
    """Считает доступную ёмкость инженера в человеко-часах на спринт.

    Формула: 80 часов (стандартный спринт) x capacity_rate инженера.

    Важно: если инженер разделён между несколькими командами, сумма его часов,
    выделенных всем командам в рамках спринта, не должна превышать
    80 x capacity_rate — распределение по командам должно учитываться
    вызывающей стороной (scheduler.py).

    Args:
        engineer_id: идентификатор инженера.
        sprint: номер спринта.
        engineers: список профилей инженеров (Engineers_profiles), см.
            backend/ingestion/models.py::Engineer.

    Returns:
        Доступная ёмкость инженера в человеко-часах на указанный спринт.
    """
    raise NotImplementedError
