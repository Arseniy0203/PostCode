"""Сроки найма и переключения, ручной перенос задачи в следующий квартал,
бэклог следующего квартала."""

from __future__ import annotations

import copy

import pytest

from backend.recalculation.baseline import save_baseline
from backend.recalculation.next_quarter import next_quarter_backlog
from backend.recalculation.pipeline import build_week0, load_dataset, run_quarter
from backend.recalculation.recalc import apply_sprint_fact, mark_deferred, plan_tasks
from backend.recalculation.scenarios import build_fact, simulate
from backend.recalculation.summary import quarter_summary
from backend.risk.alerts import check_critical, check_resource_deficit, duration_sprints, task_timing
from tests.test_recalc import CAPACITY, DEPS, ENGINEERS, KW, _get, _plan, fake_planner


def _t(tid, team, hh, **kw):
    return {"task_id": tid, "prodf_id": kw.pop("prodf", "PRODF-1"), "team_id": team,
            "status": kw.pop("status", "ToDo"), "decision": kw.pop("decision", "postponed"),
            "estimated_hh": hh, **kw}


# --- сроки ----------------------------------------------------------------------


def test_duration_uses_biggest_role_one_person_each():
    assert duration_sprints(_t("X", "T", {"Java": 170, "Аналитик": 40})) == 3
    assert duration_sprints(_t("X", "T", {})) == 1


def test_duration_accounts_for_onboarding_ramp():
    task = _t("X", "T", {"Java": 160})
    assert duration_sprints(task) == 2                     # опытный: 80 + 80
    assert duration_sprints(task, (0.5, 0.75)) == 3        # новичок: 40 + 60 + 80


def test_hire_fits_early_but_not_late_in_quarter():
    task = _t("X", "T", {"Java": 160})              # новичку 3 спринта → начать не позже 4-го
    assert task_timing(task, "hire", now=0) == {"task_id": "X", "kind": "hire", "ready": 4,
                                                 "latest": 4, "duration": 3, "split": False,
                                                 "fits": True}
    assert task_timing(task, "hire", now=0, oversized=True)["fits"] is False
    assert task_timing(task, "hire", now=1)["fits"] is False    # выйдет к 5-му
    assert task_timing(task, "borrow", now=2)["fits"] is True   # переключение — к 4-му, 60+80+80


def test_hired_engineer_capacity_uses_average_ramp():
    from backend.recalculation.recalc import active_resources

    hired = {"H": {"engineer_id": "H", "team_id": "T", "role": "Java", "capacity_rate": 1.0,
                   "joined_sprint": 2}}
    _, capacity = active_resources([], {"engineer_hh": {}}, set(), hired, first_sprint=2)
    assert capacity["engineer_hh"]["H"] == pytest.approx(80 * (0.5 + 0.75 + 1 + 1 + 1) / 5, abs=0.1)
    _, later = active_resources([], {"engineer_hh": {}}, set(), hired, first_sprint=4)
    assert later["engineer_hh"]["H"] == 80.0


def test_hiring_alert_marks_tasks_to_defer_when_hire_is_too_late():
    plan = save_baseline({"current": [
        _t("SHORT", "Team-Delta", {"Руководитель проекта": 40}),
        _t("LONG", "Team-Delta", {"Руководитель проекта": 400}),
    ]})
    plan["current_sprint"] = 1
    [alert] = check_resource_deficit(plan, [])
    assert alert.defer_task_ids == ("LONG",)
    assert "успевают при найме" in alert.recommendation and "SHORT" in alert.recommendation
    assert "не успеть в квартале: LONG" in alert.recommendation
    assert "перенести в следующий квартал (01.10.2026)" in alert.recommendation


# --- ручной перенос ------------------------------------------------------------


def test_defer_via_fact_frees_capacity_and_blocks_dependents():
    plan = _plan()
    apply_sprint_fact(plan, {"tasks": {"B": {"defer": True, "note": "ждём решение заказчика"}}}, **KW)
    b, c = _get(plan, "B"), _get(plan, "C")
    assert b["deferred"] and b["decision"] == "postponed"
    assert b["target_option"] == "NOT_IN_QUARTER" and "ждём решение заказчика" in b["reason_detail"]
    assert c["decision"] == "postponed"
    red = next(a for a in plan["alerts"] if a.task_id == "B")
    assert "решением руководителя" in red.description
    assert any(ch["kind"] == "deferred" for ch in plan["history"][-1]["changes"])
    assert not [a for a in plan["alerts"] if a.category in ("hiring", "capacity") and "B" in a.affected_task_ids]


def test_deferred_task_can_be_returned():
    plan = _plan()
    apply_sprint_fact(plan, {"tasks": {"C": {"status": "Deferred"}}}, **KW)
    assert _get(plan, "C")["decision"] == "postponed"
    apply_sprint_fact(plan, {"tasks": {"C": {"defer": False}}}, **KW)
    assert _get(plan, "C")["decision"] == "included"
    assert any(ch["kind"] == "returned" for ch in plan["history"][-1]["changes"])


def test_deferred_in_progress_task_keeps_partial_target():
    plan = _plan()
    apply_sprint_fact(plan, {"tasks": {"B": {"status": "InProgress", "spent_hh": {"Разработчик Java": 40}}}},
                      **KW)
    apply_sprint_fact(plan, {"tasks": {"B": {"defer": True}}}, **KW)
    b = _get(plan, "B")
    assert b["target_option"] == "FUNC_PARTIAL" and "Выполнено 25%" in b["reason_detail"]
    row = next(r for r in quarter_summary(plan)["tasks"] if r["task_id"] == "B")
    assert row["outcome"] == "deferred" and row["label"].startswith("3.")


def test_week0_defer_changes_baseline_and_reports_unknown_ids():
    tasks = copy.deepcopy(_plan()["current"])
    assert mark_deferred(tasks, {"D": "не приоритет", "NOPE": ""}) == ["NOPE"]
    planned = {t["task_id"]: t for t in plan_tasks(tasks, CAPACITY, DEPS, ENGINEERS, planner=fake_planner)}
    assert planned["D"]["decision"] == "postponed" and "не приоритет" in planned["D"]["reason_detail"]


def test_week0_deferred_task_is_not_red():
    tasks = copy.deepcopy(_plan()["current"])
    mark_deferred(tasks, ["D"])
    plan = save_baseline({"current": plan_tasks(tasks, CAPACITY, DEPS, ENGINEERS, planner=fake_planner)})
    assert check_critical(plan) == []


def test_defer_scenario_picks_lowest_priority_not_started_task():
    plan = _plan()
    for task, rung in zip(plan["current"], (90, 80, 70, 60, 50)):
        task["rung"] = rung
    fact = build_fact(plan, "defer_task", 1, dependencies=DEPS, engineers=ENGINEERS)
    assert fact["scenario_target"] == "C"
    simulate(plan, "defer_task", 1, dependencies=DEPS, engineers=ENGINEERS, capacity=CAPACITY,
             planner=fake_planner)
    assert _get(plan, "C")["deferred"]


# --- бэклог следующего квартала -----------------------------------------------


def test_next_quarter_backlog_groups_reasons_and_prepares_hires():
    plan = save_baseline({"current": [
        _t("NEED-PM", "Team-Delta", {"Руководитель проекта": 40}),
        _t("WAITS", "Team-Delta", {"Аналитик": 10}),
        _t("BIG", "Team-X", {"Java": 10}, estimation_sp=13),
        _t("MANUAL", "Team-X", {"Java": 10}, deferred=True),
        _t("OK", "Team-X", {"Java": 10}, decision="included", planned_end_sprint=2),
    ]})
    plan["capacity"] = {"team_sp": {"Team-X": 8.4, "Team-Delta": 7.6}}
    engineers = [{"engineer_id": "E1", "team_id": "Team-X", "role": "Java"},
                 {"engineer_id": "E2", "team_id": "Team-Delta", "role": "Аналитик"}]
    deps = [{"blocking_task_id": "NEED-PM", "blocked_task_id": "WAITS"}]
    backlog = next_quarter_backlog(plan, engineers, deps)
    kinds = {r["task_id"]: r["kind"] for r in backlog["tasks"]}
    assert kinds == {"NEED-PM": "no_role", "WAITS": "blocked", "BIG": "oversized", "MANUAL": "manual"}
    hire = next(p for p in backlog["prepare"] if p["action"] == "hire")
    assert hire["role"] == "Руководитель проекта" and hire["also_unblocks"] == ["WAITS"]
    assert "начать поиск не позже 20.08.2026" in hire["headline"]
    assert any(p["action"] == "split" and p["tasks"] == ["BIG"] for p in backlog["prepare"])
    assert backlog["next_quarter_start"] == "2026-10-01"


def test_next_quarter_hire_is_urgent_late_in_quarter():
    plan = save_baseline({"current": [_t("NEED-PM", "Team-Delta", {"Руководитель проекта": 40})]})
    plan["current_sprint"] = 5
    [hire] = next_quarter_backlog(plan, [])["prepare"]
    assert "немедленно" in hire["headline"]


def test_real_dataset_report_has_next_quarter_and_defer_works():
    report = run_quarter(until_sprint=0, with_hiring=False, defer=["KP-0000"])
    nq = report["next_quarter"]
    assert nq["prepare"][0]["role"] == "Руководитель проекта"
    assert any(r["task_id"] == "KP-0000" and r["kind"] == "manual" for r in nq["tasks"])
    plan = build_week0(load_dataset(), defer={"NOPE": ""})
    assert plan["warnings"] == ["Перенос NOPE пропущен: такой задачи нет."]
