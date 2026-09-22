# Ответственный: Человек 4 (frontend + tests + docs)
"""Русские подписи и форматирование чисел — общие для всех страниц."""

from __future__ import annotations

from typing import Any

DECISION_LABELS = {
    "included": "Включено",
    "postponed": "Перенесено",
    "cancelled": "Отменено",
}

STATUS_LABELS = {
    "ToDo": "В очереди",
    "InProgress": "В работе",
    "Done": "Выполнено",
    "Cancelled": "Отменено",
}


def decision_label(task: dict[str, Any]) -> str:
    """Решение по задаче словами; Done до квартала — не решение планировщика."""
    decision = task.get("decision")
    if decision in DECISION_LABELS:
        return DECISION_LABELS[decision]
    if task.get("status") == "Done":
        return "Закрыта до квартала"
    return "Решение не принято"


def hours(value: Any) -> str:
    """Часы без хвостов вида 14.57: до целых, дробь — только у малых значений."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{number:.1f}".rstrip("0").rstrip(".") if number < 10 else f"{round(number):d}"


def sprint_range(start: Any, end: Any) -> str:
    if not start:
        return ""
    return f"спринт {start}" if not end or end == start else f"спринты {start}–{end}"


def sprint_moment(sprint: Any) -> str:
    """Когда обнаружено: 0 — Неделя 0, иначе после спринта N."""
    return "Неделя 0" if not sprint else f"после спринта {sprint}"
