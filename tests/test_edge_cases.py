# Ответственный: Человек 4 (frontend + tests + docs)
"""Тесты граничных сценариев планировщика (pytest).

Пустые тест-функции — реализация будет добавлена командой по мере
готовности соответствующей бизнес-логики.
"""

from __future__ import annotations


def test_task_requires_missing_specialist() -> None:
    """Задача требует роль/компетенцию, отсутствующую в команде.

    Ожидается: задача помечается как postponed/cancelled с
    reason_code=NO_RESOURCES.
    """
    raise NotImplementedError


def test_task_stretches_across_sprints_single_senior() -> None:
    """Задача растягивается на несколько спринтов из-за нехватки часов роли.

    Эталонный сценарий — задача DB-202 из датасета: единственный сеньор
    по роли не может закрыть всю оценку ЧЧ в рамках одного спринта, задача
    должна растянуться на N спринтов (см. planning/scheduler.py, шаг 4).
    """
    raise NotImplementedError


def test_dependency_chain_shifts_deadline() -> None:
    """Сдвиг блокирующей задачи сдвигает срок цепочки зависимых задач.

    Ожидается: генерируется Alert type="yellow" через
    backend/risk/alerts.py::check_cascade.
    """
    raise NotImplementedError


def test_part_time_engineer_hours_not_exceeded() -> None:
    """Инженер с частичной ставкой (0.5) разделён между 2 командами.

    Ожидается: суммарные часы инженера по обеим командам в рамках спринта
    не превышают 80 x capacity_rate (см. planning/capacity.py::engineer_capacity_hh).
    """
    raise NotImplementedError


def test_recalc_does_not_mutate_baseline() -> None:
    """Пересчёт плана (apply_sprint_fact) не должен изменять Plan.baseline.

    Ожидается: после нескольких вызовов recalculation/recalc.py::apply_sprint_fact
    plan.baseline остаётся идентичным изначальному снимку, сделанному
    baseline.py::save_baseline.
    """
    raise NotImplementedError


def test_bus_factor_one_linked_to_task() -> None:
    """Компетенция с Bus Factor = 1 корректно связывается с задачами плана.

    Ожидается: backend/starmap/bus_factor.py::link_risk_to_tasks возвращает
    задачи, зависящие от единственного носителя компетенции.
    """
    raise NotImplementedError
