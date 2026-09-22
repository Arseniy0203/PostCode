# Ответственный: Человек 2 (recalculation + risk)
"""Тесты пересчёта, сценариев и отчёта о качестве данных."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest

from backend.recalculation.baseline import save_baseline
from backend.recalculation.recalc import SprintOrderError, apply_sprint_fact, prepare_tasks
from backend.recalculation.scenarios import build_fact, simulate
from backend.risk.data_quality import check_data_quality

HOURS_PER_SPRINT = 80.0


def fake_planner(tasks, capacity, dependencies, engineers, estimates=None,
                 start_sprint=1, locked_tasks=None):
    """Упрощённый планировщик: длительность = часы / 80, зависимости с зазором 1 спринт."""
    locked = set(locked_tasks or [])
    ends = {t["task_id"]: t.get("forecast_end_sprint") or 0 for t in tasks if t["task_id"] in locked}
    blockers = {}
    for d in dependencies:
        blockers.setdefault(d["blocked_task_id"], []).append(d["blocking_task_id"])
    result = {t["task_id"]: copy.deepcopy(t) for t in tasks if t["task_id"] in locked}
    pending = [t for t in tasks if t["task_id"] not in locked]
    while pending:
        progressed = False
        for task in list(pending):
            deps = blockers.get(task["task_id"], [])
            if any(b not in ends and b in {p["task_id"] for p in pending} for b in deps):
                continue
            pending.remove(task)
            progressed = True
            item = copy.deepcopy(task)
            if any(b not in ends for b in deps):
                item.update(decision="postponed", reason_detail="блокирующая задача не в плане")
                result[task["task_id"]] = item
                continue
            hours = sum(task["estimated_hh"].values())
            start = max([start_sprint] + [ends[b] + 1 for b in deps if b in ends])
            end = start + max(1, math.ceil(hours / HOURS_PER_SPRINT)) - 1
            if end > 6:
                item.update(decision="postponed", reason_detail="не влезает в квартал")
            else:
                n = end - start + 1
                item.update(decision="included", planned_start_sprint=start,
                            planned_end_sprint=end, forecast_end_sprint=end,
                            allocation={s: {r: h / n for r, h in task["estimated_hh"].items()}
                                        for s in range(start, end + 1)})
                ends[task["task_id"]] = end
            result[task["task_id"]] = item
        if not progressed:
            break
    return {"current": list(result.values()), "baseline": list(result.values())}


DEPS = [
    {"blocking_task_id": "A", "blocked_task_id": "B", "dependency_type": "depends on"},
    {"blocking_task_id": "B", "blocked_task_id": "C", "dependency_type": "depends on"},
]
ENGINEERS: list[dict] = []
CAPACITY = {"team_sp": {}, "engineer_hh": {"ENG-1": 80.0}}
KW = dict(dependencies=DEPS, engineers=ENGINEERS, capacity=CAPACITY, planner=fake_planner)


def _task(tid, hours, status="ToDo", prodf="PRODF-1", sp=6):
    return {"task_id": tid, "prodf_id": prodf, "team_id": "Team-X", "status": status,
            "estimation_sp": sp, "estimated_hh": {"Разработчик Java": hours}, "spent_hh": {}}


def _plan():
    tasks = [_task("A", 80), _task("B", 160), _task("C", 80), _task("D", 80, prodf="PRODF-2"),
             _task("OLD", 50, status="Done")]
    planned = fake_planner(tasks, CAPACITY, DEPS, ENGINEERS, locked_tasks=["OLD"])
    return save_baseline(planned)


def _get(plan, tid):
    return next(t for t in plan["current"] if t["task_id"] == tid)


# --- порядок спринтов и неизменность baseline ----------------------------------


def test_recalc_requires_baseline():
    with pytest.raises(ValueError):
        apply_sprint_fact({"current": []}, {}, **KW)


def test_fact_only_for_next_sprint_no_future_no_replay():
    plan = _plan()
    with pytest.raises(SprintOrderError):
        apply_sprint_fact(plan, {}, 2, **KW)
    apply_sprint_fact(plan, {}, 1, **KW)
    with pytest.raises(SprintOrderError):
        apply_sprint_fact(plan, {}, 1, **KW)


def test_recalc_does_not_mutate_baseline():
    plan = _plan()
    snapshot = copy.deepcopy(plan["baseline"])
    simulate(plan, "key_task_delay", 3, dependencies=DEPS, engineers=ENGINEERS,
             capacity=CAPACITY, planner=fake_planner)
    assert plan["baseline"] == snapshot


# --- применение факта ----------------------------------------------------------


def test_done_task_is_frozen_with_actual_end():
    plan = _plan()
    fact = {"tasks": {"A": {"status": "Done", "spent_hh": {"Разработчик Java": 80}}}}
    apply_sprint_fact(plan, fact, **KW)
    a = _get(plan, "A")
    assert a["actual_end_sprint"] == 1 and a["forecast_end_sprint"] == 1
    assert _get(plan, "B")["planned_start_sprint"] == 2


def test_planner_gets_remaining_hours_and_proportional_sp():
    seen = {}

    def spy(tasks, *args, **kwargs):
        seen.update({t["task_id"]: t for t in tasks})
        return fake_planner(tasks, *args, **kwargs)

    plan = _plan()
    fact = {"tasks": {"B": {"status": "InProgress", "spent_hh": {"Разработчик Java": 40}}}}
    apply_sprint_fact(plan, fact, **{**KW, "planner": spy})
    assert seen["B"]["estimated_hh"] == {"Разработчик Java": 120}
    assert seen["B"]["estimation_sp"] == pytest.approx(6 * 120 / 160)
    assert _get(plan, "B")["estimated_hh"] == {"Разработчик Java": 160}
    assert _get(plan, "B")["spent_hh"] == {"Разработчик Java": 40}


def test_unclosed_task_shifts_chain_and_explains_why():
    plan = _plan()
    fact = {"tasks": {"A": {"status": "InProgress", "spent_hh": {"Разработчик Java": 40}}}}
    apply_sprint_fact(plan, fact, **KW)

    assert [a.type for a in plan["alerts"] if a.type == "yellow"] == ["yellow"]
    changes = {c["task_id"]: c for c in plan["history"][-1]["changes"]}
    assert changes["A"]["cause_text"] == "задача не закрыта в плановом спринте 1"
    assert changes["B"]["cause_task_id"] == "A"


def test_fact_for_unknown_or_closed_task_becomes_warning():
    plan = _plan()
    apply_sprint_fact(plan, {"tasks": {"SRV-4019": {"status": "Done"}, "OLD": {"status": "Done"}}}, **KW)
    assert len(plan["warnings"]) == 2


def test_unavailable_engineer_keeps_role_but_has_zero_capacity():
    seen = {}

    def spy(tasks, capacity, dependencies, engineers, **kwargs):
        seen["capacity"], seen["engineers"] = capacity, engineers
        return fake_planner(tasks, capacity, dependencies, engineers, **kwargs)

    plan = _plan()
    engineers = [{"engineer_id": "ENG-1", "team_id": "Team-X", "role": "Разработчик Java"}]
    apply_sprint_fact(plan, {"engineers_unavailable": ["ENG-1"]},
                      **{**KW, "engineers": engineers, "planner": spy})
    assert seen["capacity"]["engineer_hh"]["ENG-1"] == 0
    [eng] = seen["engineers"]
    assert eng["engineer_id"] == "ENG-1" and eng["capacity_rate"] == 0.0 and eng["unavailable"]
    assert CAPACITY["engineer_hh"]["ENG-1"] == 80.0


def test_quarter_ends_after_sprint_6():
    plan = simulate(_plan(), "on_track", 6, dependencies=DEPS, engineers=ENGINEERS,
                    capacity=CAPACITY, planner=fake_planner)
    with pytest.raises(ValueError):
        apply_sprint_fact(plan, {}, **KW)


# --- сценарии ------------------------------------------------------------------


def test_on_track_closes_everything_without_red_alerts():
    plan = simulate(_plan(), "on_track", 6, dependencies=DEPS, engineers=ENGINEERS,
                    capacity=CAPACITY, planner=fake_planner)
    assert all(t["status"] == "Done" for t in plan["current"])
    assert not [a for h in plan["history"] for a in h["alerts"] if a["type"] == "red"]


def test_key_task_delay_targets_head_of_longest_chain():
    plan = _plan()
    fact = build_fact(plan, "key_task_delay", 1, dependencies=DEPS, engineers=ENGINEERS)
    assert fact["scenario_target"] == "A"
    assert fact["tasks"]["A"]["status"] == "InProgress"


def test_unknown_scenario_rejected():
    with pytest.raises(ValueError):
        build_fact(_plan(), "nope", 1, dependencies=DEPS, engineers=ENGINEERS)


# --- подготовка данных и качество данных --------------------------------------


def test_prepare_tasks_uses_calculator_and_spent_roles():
    tasks = [{"task_id": "DB-202", "estimated_hh": 999}]
    estimates = [
        {"task_id": "DB-202", "role": "Разработчик .net", "hours": 130},
        {"task_id": "DB-202", "role": "Аналитик", "hours": 0},
        {"task_id": "DB-202", "role": "Итого по ролям", "hours": 130},
    ]
    spent = [{"task_id": "DB-202", "Исходное время": 324, "Разработчик .net": 40, "Аналитик": 0}]
    [task] = prepare_tasks(tasks, estimates, spent)
    assert task["estimated_hh"] == {"Разработчик .net": 130}
    assert task["spent_hh"] == {"Разработчик .net": 40}


def test_data_quality_reports_ghosts_duplicates_and_hh_mismatch():
    tasks = [{"task_id": "SRV-4011", "prodf_id": "PRODF-7126", "estimated_hh": 160},
             {"task_id": "SRV-4012", "prodf_id": "PRODF-7126", "estimated_hh": 240}]
    estimates = [{"task_id": "SRV-4011", "role": "Аналитик", "hours": 160},
                 {"task_id": "SRV-4012", "role": "Аналитик", "hours": 270},
                 {"task_id": "SRV-4013", "role": "Аналитик", "hours": 30}]
    deps = [{"blocking_task_id": "SRV-4011", "blocked_task_id": "SRV-4019"},
            {"blocking_task_id": "SRV-4011", "blocked_task_id": "SRV-4012", "dependency_type": "depends on"},
            {"blocking_task_id": "SRV-4011", "blocked_task_id": "SRV-4012", "dependency_type": "is required for"}]
    alerts = check_data_quality(tasks, estimates, deps)
    text = " | ".join(a.description for a in alerts)
    assert all(a.type == "info" and a.category == "data_quality" for a in alerts)
    assert "призрак SRV-4019" in text
    assert "указана 2 раза" in text
    assert "SRV-4013" in text
    assert "240 ч" in text and "270 ч" in text
    assert len(alerts) == 4


# --- интеграция с планировщиком Матвея на реальных данных ---------------------

DATA = Path(__file__).resolve().parents[1] / "data" / "processed"


def test_pipeline_on_real_dataset_with_real_planner():
    from backend.planning import scheduler

    if "start_sprint" not in scheduler.build_baseline_plan.__code__.co_varnames:
        pytest.skip("планировщик без start_sprint/locked_tasks")
    from backend.planning.capacity import engineer_capacity_hh, team_capacity_sp

    load = lambda name: json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))
    engineers, history, deps = load("engineers_profiles"), load("team_history"), load("task_dependencies")
    tasks = prepare_tasks(load("tasks"), load("estimates"), load("spent_time_roles"))
    capacity = {
        "team_sp": {t: team_capacity_sp(t, history) for t in {h["team_id"] for h in history}},
        "engineer_hh": {e["engineer_id"]: engineer_capacity_hh(e["engineer_id"], 1, engineers)
                        for e in engineers},
    }
    plan = save_baseline(scheduler.build_baseline_plan(tasks, capacity, deps, engineers))
    snapshot = copy.deepcopy(plan["baseline"])
    simulate(plan, "key_task_delay", 3, dependencies=deps, engineers=engineers, capacity=capacity)
    assert plan["current_sprint"] == 3 and len(plan["history"]) == 4
    assert plan["baseline"] == snapshot
