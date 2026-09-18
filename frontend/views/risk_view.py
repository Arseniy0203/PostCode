# Ответственный: Человек 4 (frontend + tests + docs)
"""Страница списка алертов (рисков)."""

from __future__ import annotations

from typing import Any


def render_risk_view(alerts: list[Any]) -> None:
    """Отрисовывает список алертов с привязкой к PRODF-цели.

    Args:
        alerts: список Alert (см. contracts/alert_schema.md), каждый содержит
            prodf_id_at_risk для привязки к PRODF-цели.
    """
    raise NotImplementedError
