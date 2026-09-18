# Ответственный: Человек 4 (frontend + tests + docs)
"""Страница звёздной карты компетенций."""

from __future__ import annotations

from typing import Any


def render_star_map_view(competencies: Any) -> None:
    """Отрисовывает таблицу компетенций.

    Подсвечивает компетенции с Bus Factor = 1 (см.
    backend/starmap/bus_factor.py::find_single_points_of_failure) и связывает
    их с задачами текущего плана (см. bus_factor.py::link_risk_to_tasks).

    Args:
        competencies: данные звёздной карты (профили команд/инженеров,
            Bus Factor по компетенциям, связка с задачами).
    """
    raise NotImplementedError
