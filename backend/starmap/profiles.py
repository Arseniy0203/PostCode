# Ответственный: Человек 3 (starmap + explainability)
"""Сборка профилей команд и инженеров для звёздной карты компетенций."""

from __future__ import annotations

from typing import Any


def build_team_profiles(engineers: list[dict[str, Any]]) -> dict[str, Any]:
    """Собирает агрегированные профили команд по компетенциям инженеров.

    Args:
        engineers: список профилей инженеров (Engineers_profiles), см.
            backend/ingestion/models.py::Engineer.

    Returns:
        Словарь team_id -> профиль команды (напр. агрегированный список
        компетенций и число инженеров-носителей каждой из них).
    """
    raise NotImplementedError


def build_engineer_profiles(engineers: list[dict[str, Any]]) -> dict[str, Any]:
    """Собирает профили отдельных инженеров для отображения на карте.

    Args:
        engineers: список профилей инженеров (Engineers_profiles).

    Returns:
        Словарь engineer_id -> профиль инженера (команда, ставка,
        заявленные компетенции skills_declared).
    """
    raise NotImplementedError
