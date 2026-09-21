# Ответственный: Человек 3 (KPI)
"""Тесты KPI (backend/kpi/metrics.py): формулы и неизменность знаменателя."""

from __future__ import annotations

import copy

import pytest

from backend.kpi.metrics import kpi_breakdown, pi_predictability, say_do_ratio
from backend.recalculation import kpi as legacy_kpi


def _task(task_id, prodf_id, sp, planned_end, status="ToDo", actual_end=None,
          decision="included", forecast_end=None):
    return {
        "task_id": task_id, "prodf_id": prodf_id, "estimation_sp": sp,
        "planned_start": 1, "planned_end": planned_end, "status": status,
        "actual_end": actual_end, "forecast_end": forecast_end, "decision": decision,
    }


@pytest.fixture
def baseline():
    return [
        _task("A1", "PRODF-1", 5, 1),
        _task("A2", "PRODF-1", 3, 2),
        _task("B1", "PRODF-2", 8, 2),
        _task("C1", "PRODF-3", 4, 3),
        _task("X1", "PRODF-9", 6, 7, decision="postponed"),  # не в плане
    ]


@pytest.fixture
def current(baseline):
    cur = copy.deepcopy(baseline)
    by_id = {t["task_id"]: t for t in cur}
    by_id["A1"].update(status="Done", actual_end=1)
    by_id["A2"].update(status="Done", actual_end=2)
    by_id["B1"].update(status="Done", actual_end=3)
    by_id["C1"].update(status="InProgress", forecast_end=5)
    return cur


def test_pi_fact_formula(baseline, current) -> None:
    # Включено 3 инициативы, фактически завершены PRODF-1 и PRODF-2.
    assert pi_predictability(baseline, current) == pytest.approx(2 / 3 * 100)


def test_pi_forecast_differs_from_fact(baseline, current) -> None:
    # C1 ещё не Done, но прогнозно успевает к 6-му спринту.
    assert pi_predictability(baseline, current, mode="forecast") == pytest.approx(100.0)


def test_partial_prodf_not_counted(baseline, current) -> None:
    next(t for t in current if t["task_id"] == "A2").update(status="InProgress", actual_end=None)
    details = kpi_breakdown(baseline, current)["pi_predictability"]["fact"]
    assert "PRODF-1" in details["not_completed_prodf"]
    assert details["not_completed_prodf"]["PRODF-1"] == ["A2"]


def test_done_after_quarter_not_counted(baseline, current) -> None:
    next(t for t in current if t["task_id"] == "B1").update(actual_end=7)
    assert pi_predictability(baseline, current) == pytest.approx(1 / 3 * 100)


def test_say_do_formula(baseline, current) -> None:
    # Спринт 2: по baseline запланированы A2 (3) + B1 (8) = 11 SP;
    # фактически в спринте 2 сделана только A2 (3 SP).
    assert say_do_ratio(baseline, current, 2) == pytest.approx(3 / 11 * 100)
    assert say_do_ratio(baseline, current, 3) == pytest.approx(8 / 4 * 100)
    assert say_do_ratio(baseline, current, 6) == 0.0


def test_denominator_unchanged_after_recalc(baseline, current) -> None:
    before = kpi_breakdown(baseline, current)
    snapshot = copy.deepcopy(baseline)

    # Пересчёт: задачи переносятся, отменяются, появляются новые.
    for t in current:
        t["planned_end"] = 6
    current[2]["decision"] = "cancelled"
    current.append(_task("N1", "PRODF-7", 13, 2, status="Done", actual_end=2))

    after = kpi_breakdown(baseline, current)
    assert baseline == snapshot
    pi_b, pi_a = before["pi_predictability"]["fact"], after["pi_predictability"]["fact"]
    assert pi_a["denominator"] == pi_b["denominator"] == 3
    for s_b, s_a in zip(before["say_do"], after["say_do"]):
        assert s_a["planned_sp"] == s_b["planned_sp"]
    # Числитель при этом меняется: новая задача N1 сделана во 2-м спринте.
    assert after["say_do"][1]["done_sp"] == before["say_do"][1]["done_sp"] + 13


def test_stretched_task_rows_collapsed() -> None:
    base = [
        {**_task("DB-202", "PRODF-7124", 8, 2), "sprint_number": 2},
        {**_task("DB-202", "PRODF-7124", 8, 3), "sprint_number": 3},
    ]
    assert say_do_ratio(base, [], 3) == 0.0
    details = kpi_breakdown(base, [])["say_do"]
    assert details[2]["planned_sp"] == 8  # SP один раз, в спринт окончания
    assert details[1]["planned_sp"] == 0


def test_empty_baseline_gives_zero() -> None:
    assert pi_predictability([], []) == 0.0


def test_legacy_import_path() -> None:
    assert legacy_kpi.pi_predictability is pi_predictability


def test_reads_sprint_fields_not_excel_dates() -> None:
    """Сроки берутся из *_sprint; сырые даты Excel игнорируются.

    Планировщик пишет planned_end_sprint / forecast_end_sprint, а одноимённые
    поля без суффикса в датасете содержат даты Excel (46097.45). Если читать
    их, Say/Do всегда 0, а Done-задачи выглядят завершёнными после квартала.
    """
    base = [{"task_id": "A1", "prodf_id": "P1", "estimation_sp": 5,
             "planned_end_sprint": 2, "planned_end": 46097.457638888889,
             "status": "ToDo", "decision": "included"}]
    cur = [{"task_id": "A1", "prodf_id": "P1", "estimation_sp": 5,
            "planned_end_sprint": 2, "planned_end": 46097.457638888889,
            "actual_end_sprint": 2, "actual_end": 46168.475694444445,
            "status": "Done", "decision": "included"}]
    assert say_do_ratio(base, cur, 2) == pytest.approx(100.0)
    assert pi_predictability(base, cur) == pytest.approx(100.0)


def test_excel_date_alone_is_not_a_sprint() -> None:
    # Нет *_sprint, только дата: срок считается неизвестным, а не спринтом 46168.
    cur = [{"task_id": "A1", "prodf_id": "P1", "estimation_sp": 5,
            "planned_end": 46097.45, "actual_end": 46168.47, "status": "Done"}]
    details = kpi_breakdown(cur, cur)["say_do"]
    assert all(s["planned_sp"] == 0 for s in details)


def test_sprint_number_as_string() -> None:
    base = [{"task_id": "A1", "prodf_id": "P1", "estimation_sp": 4,
             "planned_end_sprint": "3", "status": "ToDo"}]
    cur = [{"task_id": "A1", "prodf_id": "P1", "estimation_sp": 4,
            "actual_end_sprint": "3", "status": "Done"}]
    assert say_do_ratio(base, cur, 3) == pytest.approx(100.0)


def test_prodf_id_from_excel_column_name() -> None:
    # Парсер отдаёт задачи с исходным заголовком «Номер инициативы».
    base = [{"task_id": "A1", "Номер инициативы": "PRODF-1", "estimation_sp": 5,
             "planned_end_sprint": 1, "status": "ToDo", "decision": "included"}]
    cur = [{"task_id": "A1", "Номер инициативы": "PRODF-1", "estimation_sp": 5,
            "planned_end_sprint": 1, "actual_end_sprint": 1, "status": "Done",
            "decision": "included"}]
    assert pi_predictability(base, cur) == pytest.approx(100.0)


@pytest.fixture
def partial_initiative():
    """Инициатива, у которой часть задач не влезла в квартал."""
    baseline = [
        _task("A1", "PRODF-1", 5, 1, decision="included"),
        _task("A2", "PRODF-1", 8, None, decision="postponed"),
    ]
    current = [
        _task("A1", "PRODF-1", 5, 1, status="Done", actual_end=1, decision="included"),
        _task("A2", "PRODF-1", 8, None, decision="postponed"),
    ]
    return baseline, current


def test_pi_scopes_differ_for_partial_initiative(partial_initiative) -> None:
    baseline, current = partial_initiative
    # Взятое в квартал выполнено полностью...
    assert pi_predictability(baseline, current, scope="committed") == pytest.approx(100.0)
    # ...но цель заказчика не достигнута: A2 перенесена.
    assert pi_predictability(baseline, current, scope="initiative") == pytest.approx(0.0)


def test_pi_denominator_same_in_both_scopes(partial_initiative) -> None:
    baseline, current = partial_initiative
    details = kpi_breakdown(baseline, current)["pi_predictability"]
    assert details["fact"]["denominator"] == details["fact_initiative"]["denominator"] == 1
    assert details["fact_initiative"]["not_completed_prodf"]["PRODF-1"] == ["A2"]


def test_pi_scopes_equal_when_everything_committed(baseline, current) -> None:
    for mode in ("fact", "forecast"):
        assert pi_predictability(baseline, current, mode, "committed") == \
            pi_predictability(baseline, current, mode, "initiative")


def test_unknown_scope_rejected(baseline, current) -> None:
    with pytest.raises(ValueError):
        pi_predictability(baseline, current, scope="всё подряд")
