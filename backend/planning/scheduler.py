# Ответственный: Человек 1 (ingestion + planning)
"""Главный планировщик — построение baseline-плана квартала."""

from __future__ import annotations

from typing import Any


def build_baseline_plan(
    tasks: list[dict[str, Any]],
    capacity: dict[str, Any],
    dependencies: list[dict[str, Any]],
    engineers: list[dict[str, Any]],
) -> Any:
    """Строит исходный (baseline) план распределения задач по спринтам.

    Шаги алгоритма (реализуются в порядке ниже):
        1. Проверка лимита SP команды на спринт
           (см. capacity.py::team_capacity_sp).
        2. Проверка лимита ЧЧ по роли с учётом стека инженера
           (см. capacity.py::engineer_capacity_hh, поддержка деления ставки
           между командами).
        3. Проверка зависимостей: между блокирующей и блокируемой задачей
           должен быть зазор ≥ 1 спринт.
        4. Растягивание задачи на N спринтов при нехватке часов по роли
           в рамках одного спринта.
        5. Пометка задач, которые не поместились в план, как postponed
           или cancelled с обязательным reason_code
           (см. backend/explainability/reasons.py).

    Args:
        tasks: список задач (Tasks + estimated_hh).
        capacity: предрассчитанные лимиты команд/инженеров.
        dependencies: список зависимостей (Task_dependencies, после
            filter_orphan_dependencies).
        engineers: список профилей инженеров (Engineers_profiles).

    Returns:
        Plan (см. contracts/plan_schema.md) с заполненным baseline.
    """
    raise NotImplementedError
