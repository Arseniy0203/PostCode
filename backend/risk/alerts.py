# Ответственный: Человек 2 (recalculation + risk)
"""Обнаружение рисков по текущему плану и построение алертов.

Каждая функция ниже — заглушка. Любой сформированный Alert обязан содержать
prodf_id_at_risk (см. contracts/alert_schema.md).
"""

from __future__ import annotations

from typing import Any


def check_critical(plan: Any) -> list[Any]:
    """Проверяет план на критические (красные) риски.

    Условие срабатывания: прогнозируемое (forecast_end_date) завершение
    задачи выходит за неделю 12 квартала (конец PI).

    Args:
        plan: текущий Plan (используется Plan.current).

    Returns:
        Список Alert с type="red". Каждый Alert обязан содержать
        prodf_id_at_risk.
    """
    raise NotImplementedError


def check_cascade(plan: Any, dependencies: list[dict[str, Any]]) -> list[Any]:
    """Проверяет план на каскадные (жёлтые) риски по цепочкам зависимостей.

    Условие срабатывания: сдвиг задачи, от которой зависят другие задачи
    (depends_on), вызывает сдвиг сроков во всей цепочке зависимых задач.

    Args:
        plan: текущий Plan (используется Plan.current).
        dependencies: список зависимостей (Task_dependencies, после
            filter_orphan_dependencies).

    Returns:
        Список Alert с type="yellow". Каждый Alert обязан содержать
        prodf_id_at_risk.
    """
    raise NotImplementedError


def check_resource_deficit(plan: Any, engineers: list[dict[str, Any]]) -> list[Any]:
    """Проверяет план на дефицит ресурсов (оранжевые риски).

    Условие срабатывания: на следующий спринт прогнозируется нехватка
    часов по какой-либо роли относительно запланированных в плане задач.

    Args:
        plan: текущий Plan (используется Plan.current).
        engineers: список профилей инженеров (Engineers_profiles).

    Returns:
        Список Alert с type="orange". Каждый Alert обязан содержать
        prodf_id_at_risk.
    """
    raise NotImplementedError
