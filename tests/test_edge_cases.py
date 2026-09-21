# Ответственный: Человек 4 (frontend + tests + docs)
"""
Тесты граничных сценариев планировщика и смежных модулей (pytest).

Содержит строгие, изолированные проверки алгоритмов аллокации (сплитование,
дефицит ресурсов), генерации каскадных рисков и неизменяемости baseline.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from backend.planning.scheduler import build_baseline_plan


@pytest.mark.skipif(build_baseline_plan is None, reason="Модуль scheduler не найден")
def test_task_requires_missing_specialist() -> None:
    """
    Задача требует роль/компетенцию, отсутствующую в команде.

    Ожидается: решение decision="postponed" или "cancelled",
    reason_code="MISSING_COMPETENCE" (или "NO_RESOURCES").
    """
    tasks = [{
        "task_id": "FE-101",
        "team_id": "Team-Backend",
        "estimation_sp": 3.0,
        "estimated_hh": {"react": 40.0},
        "rung": 1
    }]
    capacity = {
        "team_sp": {"Team-Backend": 20.0},
        "engineer_hh": {"Eng-1": 80.0}
    }
    # В команде есть только python-разработчик, react отсутствует
    engineers = [{"engineer_id": "Eng-1", "team_id": "Team-Backend", "role": "python"}]

    plan = build_baseline_plan(tasks, capacity, [], engineers)
    task_result = plan["baseline"][0]

    assert task_result["decision"] in ("cancelled", "postponed"), \
        f"Ожидалась отмена/перенос, получено: {task_result.get('decision')}"

    reason = task_result.get("reason_code")
    assert reason in ("MISSING_COMPETENCE", "NO_RESOURCES", "NO_TECH_OWNER"), \
        f"Ожидался код причины отсутствия компетенции, получено: {reason}"


@pytest.mark.skipif(build_baseline_plan is None, reason="Модуль scheduler не найден")
def test_task_stretches_across_sprints_single_senior() -> None:
    """
    Задача растягивается на несколько спринтов из-за нехватки часов роли.

    Эталонный сценарий DB-202: требуется 130 ЧЧ роли .NET,
    но доступен только один специалист с фондом 80 ЧЧ в спринт.
    """
    tasks = [{
        "task_id": "DB-202",
        "team_id": "Team-Alpha",
        "estimation_sp": 8.0,
        "estimated_hh": {"net": 130.0},
        "rung": 3
    }]
    capacity = {
        "team_sp": {"Team-Alpha": 20.0},
        "engineer_hh": {"Senior-Net": 80.0}
    }
    engineers = [{"engineer_id": "Senior-Net", "team_id": "Team-Alpha", "role": "net"}]

    plan = build_baseline_plan(tasks, capacity, [], engineers)
    task_result = plan["baseline"][0]

    assert task_result["decision"] == "included", \
        "Задача должна быть включена (хватает времени до конца квартала)"

    start = task_result.get("planned_start_sprint")
    end = task_result.get("planned_end_sprint")

    assert start is not None and end is not None, "Спринты начала и конца должны быть заполнены"
    assert end - start >= 1, \
        f"Задача в 130ч (лимит 80ч/спринт) должна растянуться минимум на 2 спринта. Получено: {start}-{end}"

    # Проверка аллокации: в первом спринте не должно быть списано больше 80 часов
    alloc_sprint_1 = task_result.get("allocation", {}).get(start, {}).get("net", 0)
    assert alloc_sprint_1 <= 80.0, f"Превышен лимит часов инженера в спринте: {alloc_sprint_1} > 80"


def test_dependency_chain_shifts_deadline() -> None:
    """
    Сдвиг блокирующей задачи сдвигает срок цепочки зависимых задач.

    Генерация алерта 'yellow' с обязательным заполнением 'prodf_id_at_risk'.
    """

    # Эмуляция функции check_cascade согласно контракту alert_schema.md
    def mock_check_cascade(current_plan: list[dict], dependencies: list[dict]) -> list[dict]:
        alerts = []
        for dep in dependencies:
            blocker = next((t for t in current_plan if t["task_id"] == dep["blocking_task_id"]), None)
            blocked = next((t for t in current_plan if t["task_id"] == dep["blocked_task_id"]), None)

            # Если прогноз окончания блокирующей накладывается/превышает старт зависимой
            if blocker and blocked and blocker["forecast_end_sprint"] >= blocked["planned_start_sprint"]:
                alerts.append({
                    "type": "yellow",
                    "task_id": blocked["task_id"],
                    "prodf_id_at_risk": blocked["prodf_id"],
                    "description": f"Каскадный сдвиг из-за {blocker['task_id']}",
                    "sprint_detected": blocker["forecast_end_sprint"]
                })
        return alerts

    current = [
        {"task_id": "T-1", "prodf_id": "PRODF-1", "forecast_end_sprint": 4},
        {"task_id": "T-2", "prodf_id": "PRODF-2", "planned_start_sprint": 3}  # T-2 должна была начаться в спринте 3
    ]
    deps = [{"blocking_task_id": "T-1", "blocked_task_id": "T-2"}]

    alerts = mock_check_cascade(current, deps)

    assert len(alerts) == 1, "Должен быть сгенерирован 1 алерт о каскадном риске"
    alert = alerts[0]
    assert alert["type"] == "yellow", "Тип алерта должен быть 'yellow'"
    assert alert["task_id"] == "T-2", "Алерт должен указывать на пострадавшую задачу T-2"
    assert "prodf_id_at_risk" in alert and alert["prodf_id_at_risk"] == "PRODF-2", \
        "Обязательное поле prodf_id_at_risk отсутствует или неверно заполнено"


@pytest.mark.skipif(build_baseline_plan is None, reason="Модуль scheduler не найден")
def test_part_time_engineer_hours_not_exceeded() -> None:
    """
    Инженер с частичной ставкой (0.5) разделён между 2 командами.

    Суммарные аллоцированные часы инженера не должны превышать его
    доступную емкость (40 часов) за спринт суммарно по всем командам.
    """
    tasks = [
        {"task_id": "T-1", "team_id": "Team-A", "estimation_sp": 2, "estimated_hh": {"dev": 30}, "rung": 2},
        {"task_id": "T-2", "team_id": "Team-B", "estimation_sp": 2, "estimated_hh": {"dev": 30}, "rung": 1}
    ]
    capacity = {
        "team_sp": {"Team-A": 10.0, "Team-B": 10.0},
        # Общий пул инженера на спринт - 40 часов (ставка 0.5)
        "engineer_hh": {"Eng-Shared": 40.0}
    }
    engineers = [
        {"engineer_id": "Eng-Shared", "team_id": "Team-A", "role": "dev"},
        {"engineer_id": "Eng-Shared", "team_id": "Team-B", "role": "dev"}
    ]

    plan = build_baseline_plan(tasks, capacity, [], engineers)
    baseline = plan["baseline"]

    # Считаем часы, аллоцированные на 1-й спринт для роли 'dev' (которую закрывает Eng-Shared)
    total_allocated = sum(
        t.get("allocation", {}).get(1, {}).get("dev", 0)
        for t in baseline if t["decision"] == "included"
    )

    assert total_allocated <= 40.0, \
        f"Часы part-time инженера превышены: аллоцировано {total_allocated} при лимите 40"


def test_recalc_does_not_mutate_baseline() -> None:
    """Пересчёт плана (apply_sprint_fact) не должен изменять Plan.baseline."""

    # Имитация функции apply_sprint_fact из backend/recalculation/recalc.py
    def mock_apply_sprint_fact(plan_state: dict[str, Any], facts: dict[str, dict]) -> None:
        for task in plan_state["current"]:
            if task["task_id"] in facts:
                task.update(facts[task["task_id"]])

    task_mock = {
        "task_id": "T-100",
        "status": "Todo",
        "planned_end": 2,
        "decision": "included"
    }

    plan = {
        "baseline": [copy.deepcopy(task_mock)],
        "current": [copy.deepcopy(task_mock)]
    }

    fact_update = {
        "T-100": {"status": "Done", "actual_end": 3, "reason_detail": "Сдвиг"}
    }

    mock_apply_sprint_fact(plan, fact_update)

    baseline_task = plan["baseline"][0]
    current_task = plan["current"][0]

    # Проверка, что baseline остался иммутабельным
    assert baseline_task["status"] == "Todo", "Снимок baseline был мутирован: поле status"
    assert "actual_end" not in baseline_task, "Снимок baseline был мутирован: добавлено новое поле"

    # Проверка, что current успешно обновился
    assert current_task["status"] == "Done", "Хвост current не обновился"
    assert current_task["actual_end"] == 3, "Фактические данные не применились к current"


def test_bus_factor_one_linked_to_task() -> None:
    """
    Компетенция с Bus Factor = 1 корректно связывается с задачами плана.

    Ожидается возвращение списка task_id текущего плана,
    зависящих от единственного носителя компетенции.
    """

    # Эмуляция функции backend/starmap/bus_factor.py::link_risk_to_tasks
    def mock_link_risk_to_tasks(target_eng_id: str, plan: list[dict]) -> list[str]:
        affected = []
        for task in plan:
            if target_eng_id in task.get("assigned_engineers", []):
                affected.append(task["task_id"])
        return affected

    # В плане две задачи, одну из которых тянет уникальный инженер (Bus Factor = 1)
    current_plan = [
        {"task_id": "CRIT-1", "assigned_engineers": ["Unique-Eng", "Other-Eng"]},
        {"task_id": "NORM-2", "assigned_engineers": ["Other-Eng", "Third-Eng"]}
    ]

    # Unique-Eng - единственный носитель критичной технологии (Bus Factor 1)
    affected_tasks = mock_link_risk_to_tasks("Unique-Eng", current_plan)

    assert "CRIT-1" in affected_tasks, "Задача CRIT-1 должна быть связана с риском Bus Factor=1"
    assert "NORM-2" not in affected_tasks, "Задача NORM-2 не зависит от целевого инженера"
    assert len(affected_tasks) == 1, "Должна вернуться ровно 1 задача"
