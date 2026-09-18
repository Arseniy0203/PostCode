# Ответственный: Человек 3 (starmap + explainability)
# ЭТОТ ФАЙЛ — КОНТРАКТ, ИСПОЛЬЗУЕТСЯ planning/scheduler.py И
# recalculation/recalc.py, писать первым.
"""Коды причин решений планировщика по задачам (postponed/cancelled)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ReasonCode(str, Enum):
    """Справочник кодов причин, соответствующий значениям из датасета."""

    NOT_ESTIMATED = "NOT_ESTIMATED"
    NO_RESOURCES = "NO_RESOURCES"
    DEPENDENT_TEAM_NOT_READY = "DEPENDENT_TEAM_NOT_READY"
    PLAN_EXCEEDED = "PLAN_EXCEEDED"
    REQUIREMENTS_CHANGED = "REQUIREMENTS_CHANGED"


@dataclass
class Reason:
    code: ReasonCode
    detail: str


def make_reason(code: ReasonCode, detail_text: str) -> Reason:
    """Конструирует объект причины решения по задаче.

    Args:
        code: код причины из справочника ReasonCode.
        detail_text: человекочитаемое пояснение причины.

    Returns:
        Reason с указанными code и detail.
    """
    raise NotImplementedError
