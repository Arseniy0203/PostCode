"""Планировщик: жёсткие ограничения на реальном плане и правила на малых примерах."""

from __future__ import annotations

from collections import defaultdict

import pytest

from backend.planning.scheduler import build_baseline_plan, normalize_role
from backend.recalculation.pipeline import load_dataset, run_quarter
from backend.recalculation.plan_fields import remaining_hh

TOL = 0.06


@pytest.fixture(scope="module")
def real():
    data = load_dataset()
    report = run_quarter(until_sprint=0)
    return data, report["plan"]["current"]


# --- Инварианты на реальных данных ----------------------------------------------


def test_team_sp_never_exceeds_capacity(real):
    data, plan = real
    used = defaultdict(float)
    for task in plan:
        for sprint, sp in (task.get("sp_by_sprint") or {}).items():
            used[(task["team_id"], int(sprint))] += sp
    for (team, sprint), sp in used.items():
        assert sp <= data["capacity"]["team_sp"][team] + TOL, (team, sprint, sp)


def test_engineer_hours_within_rate_and_part_timers_split(real):
    data, plan = real
    rows = defaultdict(list)
    for eng in data["engineers"]:
        rows[eng["engineer_id"]].append(eng)
    per_sprint = defaultdict(float)
    per_team = defaultdict(float)
    for task in plan:
        for sprint, hours in (task.get("allocation") or {}).items():
            for eng, h in hours.items():
                per_sprint[(eng, int(sprint))] += h
                per_team[(eng, task["team_id"], int(sprint))] += h
    for (eng, sprint), h in per_sprint.items():
        limit = 80 * sum(float(r["capacity_rate"]) for r in rows[eng])
        assert h <= limit + TOL, (eng, sprint, h)
    # Парттаймер: своей команде — не больше её доли (80 × 0.5 = 40 ч).
    for (eng, team, sprint), h in per_team.items():
        own = [r for r in rows[eng] if r["team_id"] == team]
        if len(rows[eng]) > 1 and own:
            assert h <= 80 * float(own[0]["capacity_rate"]) + TOL, (eng, team, sprint, h)


def test_dependencies_start_after_blocker_ends(real):
    data, plan = real
    by_id = {t["task_id"]: t for t in plan}
    for dep in data["dependencies"]:
        a, b = by_id.get(dep["blocking_task_id"]), by_id.get(dep["blocked_task_id"])
        if not a or not b or b.get("decision") != "included" or b.get("status") == "Done":
            continue
        if a.get("status") == "Done":
            continue
        assert a.get("decision") == "included"
        assert b["planned_start_sprint"] >= a["planned_end_sprint"] + 1, (a["task_id"], b["task_id"])


def test_allocated_hours_cover_task_minus_unstaffed(real):
    _, plan = real
    for task in plan:
        if task.get("decision") != "included" or task.get("status") == "Done":
            continue
        need = sum(h for _, h in remaining_hh(task).values())
        planned = sum(h for row in task["allocation"].values() for h in row.values())
        unstaffed = sum((task.get("unstaffed_hh") or {}).values())
        assert planned + unstaffed == pytest.approx(max(need, 0), abs=1.5), task["task_id"]


def test_every_decision_has_reason_code_and_target(real):
    _, plan = real
    for task in plan:
        if task.get("status") == "Done":
            continue
        assert task["reason_code"] and task["reason_detail"] and task["target_option"], task["task_id"]


def test_real_plan_takes_almost_everything_with_partial_targets(real):
    _, plan = real
    open_tasks = [t for t in plan if t.get("status") != "Done"]
    included = [t for t in open_tasks if t["decision"] == "included"]
    assert len(included) >= 34
    partial = [t for t in included if t.get("unstaffed_hh")]
    assert partial and all(t["target_option"] in ("READY_FOR_IT", "FUNC_PARTIAL") for t in partial)
    # Задача крупнее спринтовой ёмкости команды (13 SP при 10.8) растянута, а не выброшена.
    srv4012 = next(t for t in plan if t["task_id"] == "SRV-4012")
    assert srv4012["decision"] == "included"
    assert srv4012["planned_end_sprint"] > srv4012["planned_start_sprint"]


# --- Правила на малых примерах ---------------------------------------------------


def _eng(eid, team, role, rate=1.0, skills=()):
    return {"engineer_id": eid, "team_id": team, "role": role, "capacity_rate": rate,
            "skills_declared": list(skills)}


def _task(tid, team, hh, sp=3, rung=80, summary="", status="ToDo"):
    return {"task_id": tid, "team_id": team, "estimation_sp": sp, "estimated_hh": hh,
            "rung": rung, "summary": summary, "status": status}


def _cap(teams, engineers):
    hh = defaultdict(float)
    for e in engineers:
        hh[e["engineer_id"]] += 80 * float(e["capacity_rate"])
    return {"team_sp": {t: 10.0 for t in teams}, "engineer_hh": dict(hh)}


def _plan(tasks, engineers, deps=(), **kw):
    teams = {t["team_id"] for t in tasks}
    return {t["task_id"]: t for t in
            build_baseline_plan(tasks, _cap(teams, engineers), list(deps), engineers, **kw)["current"]}


def test_missing_role_gives_partial_target_with_hire_advice():
    engineers = [_eng("E1", "A", "Разработчик Java")]
    plan = _plan([_task("T1", "A", {"Разработчик Java": 60, "Руководитель проекта": 10})], engineers)
    t = plan["T1"]
    assert t["decision"] == "included" and t["target_option"] == "READY_FOR_IT"
    assert t["unstaffed_hh"] == {"Руководитель проекта": 10}
    assert "Нанять" in t["recommendation"]


def test_strict_mode_postpones_on_any_missing_role():
    engineers = [_eng("E1", "A", "Разработчик Java")]
    plan = _plan([_task("T1", "A", {"Разработчик Java": 60, "Руководитель проекта": 10})],
                 engineers, missing_roles="postpone")
    assert plan["T1"]["decision"] == "postponed" and plan["T1"]["reason_code"] == "NO_RESOURCES"


def test_mostly_missing_roles_postpone():
    engineers = [_eng("E1", "A", "Тестировщик")]
    plan = _plan([_task("T1", "A", {"Тестировщик": 10, "Разработчик 1С": 50})], engineers)
    assert plan["T1"]["decision"] == "postponed"
    assert plan["T1"]["target_option"] == "NOT_IN_QUARTER"


def test_failed_task_returns_hours_to_pool():
    engineers = [_eng("E1", "A", "Аналитик")]
    plan = _plan([_task("BIG", "A", {"Аналитик": 1000}, rung=90),
                  _task("SMALL", "A", {"Аналитик": 10}, rung=10)], engineers)
    assert plan["BIG"]["decision"] == "postponed"
    assert plan["SMALL"]["decision"] == "included"


def test_part_timer_gives_each_team_only_its_share():
    engineers = [_eng("E1", "A", "ДевОпс", 0.5), _eng("E1", "B", "ДевОпс", 0.5)]
    plan = _plan([_task("T1", "A", {"ДевОпс": 80})], engineers)
    assert all(h <= 40 + TOL for row in plan["T1"]["allocation"].values() for h in row.values())
    assert plan["T1"]["planned_end_sprint"] == 2


def test_borrows_other_team_when_own_is_busy():
    engineers = [_eng("E1", "A", "Разработчик Java"), _eng("E2", "B", "Разработчик Java")]
    plan = _plan([_task("T1", "A", {"Разработчик Java": 150}, sp=2)], engineers)
    t = plan["T1"]
    assert t["planned_end_sprint"] == 1 and "E2" in t["assigned_engineers"]
    assert t["reason_facts"]["borrowed"] == {"E2": "B"}


def test_sp_spread_lets_big_task_stretch():
    engineers = [_eng("E1", "A", "Разработчик Java")]
    plan = _plan([_task("T1", "A", {"Разработчик Java": 120}, sp=13)], engineers)
    t = plan["T1"]
    assert t["decision"] == "included"
    assert sum(t["sp_by_sprint"].values()) == pytest.approx(13, abs=0.05)
    assert max(t["sp_by_sprint"].values()) <= 10 + TOL


def test_stack_from_summary_restricts_role_holders():
    engineers = [_eng("E1", "A", "ДевОпс", skills=["Docker"]),
                 _eng("E2", "A", "ДевОпс", skills=["Prometheus", "Grafana"])]
    plan = _plan([_task("T1", "A", {"ДевОпс": 20}, summary="Дашборды Grafana / Prometheus")],
                 engineers)
    assert plan["T1"]["assigned_engineers"] == ["E2"]
    assert plan["T1"]["reason_facts"]["stack"]


def test_cycle_is_named():
    engineers = [_eng("E1", "A", "Аналитик")]
    deps = [{"blocking_task_id": "C1", "blocked_task_id": "C2"},
            {"blocking_task_id": "C2", "blocked_task_id": "C1"}]
    plan = _plan([_task("C1", "A", {"Аналитик": 5}), _task("C2", "A", {"Аналитик": 5})],
                 engineers, deps)
    assert "цикле зависимостей" in plan["C1"]["reason_detail"]


def test_unknown_team_and_unestimated():
    engineers = [_eng("E1", "A", "Аналитик")]
    tasks = [_task("T1", "A", {}), _task("T2", "Z", {"Аналитик": 5})]
    cap = {"team_sp": {"A": 10.0}, "engineer_hh": {"E1": 80}}
    plan = {t["task_id"]: t for t in build_baseline_plan(tasks, cap, [], engineers)["current"]}
    assert plan["T1"]["reason_code"] == "NOT_ESTIMATED"
    assert "velocity" in plan["T2"]["reason_detail"]


def test_role_names_normalized():
    assert normalize_role("ДевОпс") == normalize_role("Девопс")
    assert normalize_role("Разработчик iOS") == normalize_role("Разработчик IOS")


def test_on_track_recalc_reproduces_own_plan():
    report = run_quarter("on_track", until_sprint=3)
    assert not [a for a in report["alerts"] if a["type"] in ("red", "yellow")]
    assert all(r["status"] in ("ok", "future", "no_data") for r in report["kpi"]["say_do"])
