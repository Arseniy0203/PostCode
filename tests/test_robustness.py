"""Краевые случаи: 6-й спринт, роли с разным написанием, отпуск,
синонимы статусов, новые задачи и факт в одном сообщении, часы с запятой,
неверная папка данных, JSON-отчёт, сценарии с видимым эффектом."""

from __future__ import annotations

import json

import pytest

from backend.recalculation.baseline import save_baseline
from backend.recalculation.pipeline import load_dataset, run_quarter
from backend.recalculation.plan_fields import remaining_hh
from backend.recalculation.recalc import (
    apply_sprint_fact,
    canonical_status,
    humanize_roles,
    prepare_tasks,
    to_hours,
)
from backend.recalculation.scenarios import build_fact, pick_busiest_engineer, pick_hire
from tests.test_recalc import KW, _get, _plan


def test_last_sprint_carry_over_reason_is_honest():
    plan = _plan()
    for sprint in range(1, 6):
        apply_sprint_fact(plan, {}, sprint, **KW)
    assert _get(plan, "D")["decision"] == "included"
    apply_sprint_fact(plan, {"tasks": {"D": {"status": "InProgress", "spent_hh": {"Разработчик Java": 20}}}},
                      6, **KW)
    d = _get(plan, "D")
    assert d["decision"] == "postponed" and "Квартал завершён" in d["reason_detail"]
    assert d["target_option"] == "FUNC_PARTIAL" and "Выполнено 25%" in d["reason_detail"]


def test_role_spelling_variants_do_not_inflate_remaining():
    plan = _plan()
    _get(plan, "B")["estimated_hh"] = {"ДевОпс": 100}
    apply_sprint_fact(plan, {"tasks": {"B": {"status": "InProgress", "spent_hh": {"Девопс": 30}}}}, **KW)
    b = _get(plan, "B")
    assert b["spent_hh"] == {"ДевОпс": 30.0}
    assert remaining_hh(b) == {"девопс": ("ДевОпс", 70.0)}


def test_status_synonyms_and_unknown_status_warning():
    assert canonical_status("Выполнено") == ("Done", True)
    assert canonical_status(" готово ") == ("Done", True)
    assert canonical_status("В работе") == ("InProgress", True)
    assert canonical_status("что-то") == (None, False)
    plan = _plan()
    apply_sprint_fact(plan, {"tasks": {"A": {"status": "Выполнено"}, "D": {"status": "ХЗ"}}}, **KW)
    assert _get(plan, "A")["status"] == "Done"
    assert any("не распознан" in w for w in plan["warnings"])


def test_fact_for_task_added_in_same_fact_is_applied():
    plan = _plan()
    new = {"task_id": "N2", "prodf_id": "P", "team_id": "Team-X", "estimation_sp": 3,
           "estimated_hh": {"Разработчик Java": 40}}
    apply_sprint_fact(plan, {"tasks_added": [new], "tasks": {"N2": {"status": "Done"}}}, **KW)
    assert _get(plan, "N2")["status"] == "Done" and not plan["warnings"]


def test_added_task_with_numeric_estimate_warns():
    plan = _plan()
    new = {"task_id": "N3", "prodf_id": "P", "team_id": "Team-X", "estimation_sp": 3, "estimated_hh": 40}
    apply_sprint_fact(plan, {"tasks_added": [new]}, **KW)
    assert _get(plan, "N3")["reason_code"] == "NOT_ESTIMATED"
    assert any("не по ролям" in w for w in plan["warnings"])


def test_decimal_comma_hours():
    assert to_hours("12,5") == 12.5 and to_hours(" 7 ") == 7.0 and to_hours(None) == 0.0
    assert to_hours(float("nan")) == 0.0
    [task] = prepare_tasks([{"task_id": "X"}], [{"task_id": "X", "role": "Java", "hours": "12,5"}],
                           [{"task_id": "X", "Java": "2,5"}])
    assert task["estimated_hh"] == {"Java": 12.5} and task["spent_hh"] == {"Java": 2.5}


def test_humanize_planner_role_keys():
    tasks = [{"estimated_hh": {"Руководитель проекта": 5, "Разработчик .net": 3}}]
    text = "В компании нет необходимых ролей: руководительпроекта, разработчик.net"
    assert humanize_roles(text, tasks) == ("В компании нет необходимых ролей: "
                                           "Руководитель проекта, Разработчик .net")


def test_missing_data_dir_raises():
    with pytest.raises(FileNotFoundError):
        load_dataset("C:/definitely/not/here")


def test_report_is_json_serializable():
    report = run_quarter("key_task_delay", until_sprint=2, with_hiring=False)
    text = json.dumps(report, ensure_ascii=False)
    assert "PRODF" in text


# --- сценарии с видимым эффектом -----------------------------------------------


def test_leave_picks_sole_role_holder_with_work():
    plan = save_baseline({"current": [
        {"task_id": "T", "status": "ToDo", "decision": "included", "planned_start_sprint": 2,
         "planned_end_sprint": 2, "allocation": {2: {"ENG-411": 80, "ENG-420": 40}}},
    ]})
    engineers = [{"engineer_id": "ENG-411", "role": "Тестировщик"},
                 {"engineer_id": "ENG-412", "role": "Тестировщик"},
                 {"engineer_id": "ENG-420", "role": "Разработчик .net"}]
    assert pick_busiest_engineer(plan, 2, engineers) == "ENG-420"


def test_hire_picks_role_absent_in_company():
    plan = save_baseline({"current": [
        {"task_id": "T1", "team_id": "Team-K", "status": "ToDo", "decision": "postponed",
         "estimated_hh": {"Аналитик": 100, "Руководитель проекта": 10}},
    ]})
    engineers = [{"engineer_id": "E", "team_id": "Team-L", "role": "Аналитик", "capacity_rate": 1}]
    assert pick_hire(plan, engineers) == ("Team-K", "Руководитель проекта")


def test_key_task_delay_prefers_task_ending_in_trigger_sprint():
    plan = save_baseline({"current": [
        {"task_id": "LONG", "status": "InProgress", "decision": "included", "planned_start_sprint": 1,
         "planned_end_sprint": 3, "rung": 99},
        {"task_id": "ENDS", "status": "ToDo", "decision": "included", "planned_start_sprint": 1,
         "planned_end_sprint": 1, "rung": 50},
    ]})
    assert build_fact(plan, "key_task_delay", 1, dependencies=[], engineers=[])["scenario_target"] == "ENDS"


def test_default_scenarios_show_mandatory_alerts_on_real_data():
    yellow = run_quarter("key_task_delay", until_sprint=3, with_hiring=False)
    assert any(a["type"] == "yellow" for h in yellow["history"] for a in h["alerts"])
    leave = run_quarter("engineer_leave", until_sprint=3, with_hiring=False)
    assert any(a["type"] == "red" for h in leave["history"] for a in h["alerts"])


def test_data_quality_reports_cycles_and_self_loops():
    from backend.risk.data_quality import check_data_quality

    tasks = [{"task_id": t, "prodf_id": "P", "status": "ToDo"} for t in ("A", "B", "C")]
    deps = [{"blocking_task_id": "A", "blocked_task_id": "B"},
            {"blocking_task_id": "B", "blocked_task_id": "A"},
            {"blocking_task_id": "C", "blocked_task_id": "C"}]
    text = " | ".join(a.description for a in check_data_quality(tasks, [], deps))
    assert "Цикл зависимостей: A → B → A" in text
    assert "Задача ссылается сама на себя: C → C" in text


def test_hiring_headlines_account_for_search_time():
    report = run_quarter(until_sprint=0)
    assert all("выход к спринту 4" in s["headline"] for s in report["hiring_roadmap"])


def test_empty_estimated_hh_is_not_a_mismatch():
    """В датасете без колонки estimated_hh (0 / NaN) не должно быть 37 ложных «расхождений»."""
    from backend.risk.data_quality import check_data_quality
    tasks = [{"task_id": "A", "status": "ToDo", "estimated_hh": 0.0},
             {"task_id": "B", "status": "ToDo", "estimated_hh": float("nan")},
             {"task_id": "C", "status": "ToDo", "estimated_hh": 50}]
    est = [{"task_id": t, "role": "Аналитик", "hours": 40} for t in "ABC"]
    text = " | ".join(a.description for a in check_data_quality(tasks, est, []))
    assert "A:" not in text and "B:" not in text
    assert "C: estimated_hh" in text


# --- Чужие и битые файлы ---------------------------------------------------------------

def _dataset_copy(tmp_path, edit, name="edited.xlsx"):
    """Копия датасета организаторов с правкой edit(workbook, sheet)."""
    from pathlib import Path
    openpyxl = pytest.importorskip("openpyxl")
    src = Path(__file__).resolve().parents[1] / "data" / "raw" /         "Хакатон_датасетс_правками_по_списку_вопросов_1.xlsx"
    if not src.exists():
        pytest.skip("нет датасета")
    wb = openpyxl.load_workbook(src)
    edit(wb, wb.worksheets[0])
    path = tmp_path / name
    wb.save(path)
    return path


def _row_of(ws, marker):
    return next(r for r in range(1, ws.max_row + 1)
                if isinstance(ws.cell(r, 1).value, str) and marker in ws.cell(r, 1).value)


def test_renamed_sheet_with_extra_sheet_in_front(tmp_path):
    from backend.ingestion.parse_dataset import parse_excel

    def edit(wb, ws):
        ws.title = "Данные"
        wb.create_sheet("Инструкция", 0)["A1"] = "Не данные"
    parsed = parse_excel(str(_dataset_copy(tmp_path, edit)))
    assert len(parsed["tasks"]) == 45 and parsed["engineers_profiles"]


def test_missing_optional_tables_do_not_block_upload(tmp_path):
    from backend.ingestion.parse_dataset import parse_excel

    def edit(wb, ws):
        start = _row_of(ws, "Итог разработки")
        for r in range(start, ws.max_row + 1):
            for c in range(1, ws.max_column + 1):
                ws.cell(r, c).value = None
    parsed = parse_excel(str(_dataset_copy(tmp_path, edit)))
    assert len(parsed["tasks"]) == 45


def test_text_in_story_points_is_reported_not_fatal(tmp_path):
    from backend.recalculation.pipeline import week0_from_excel

    def edit(wb, ws):
        start = _row_of(ws, "Tasks (Таблица задач и скоринга)")
        header = start + 1
        col = next(c for c in range(1, ws.max_column + 1)
                   if ws.cell(header, c).value == "estimation_sp")
        ws.cell(header + 5, col).value = "пять"
    state = week0_from_excel(_dataset_copy(tmp_path, edit).read_bytes())
    text = " ".join(a["description"] for a in state["report"]["data_quality"])
    assert "нет оценки в Story Points" in text


def test_not_a_dataset_gives_clear_reason(tmp_path):
    from backend.ingestion.parse_dataset import parse_excel
    openpyxl = pytest.importorskip("openpyxl")
    broken = tmp_path / "broken.xlsx"
    broken.write_bytes(b"not an excel file")
    with pytest.raises(ValueError, match="не читается как Excel"):
        parse_excel(str(broken))
    wb = openpyxl.Workbook()
    wb.active.append(["Имя", "Возраст"])
    other = tmp_path / "other.xlsx"
    wb.save(other)
    with pytest.raises(ValueError, match="не похож на датасет"):
        parse_excel(str(other))


def test_formula_ids_survive_resave_without_cached_values(tmp_path):
    """ID в Spent_time_roles — формулы «=B24». Файл, сохранённый не из Excel, хранит
    только формулу без значения: парсер должен сам вычислить ссылку."""
    from backend.ingestion.parse_dataset import parse_excel

    path = _dataset_copy(tmp_path, lambda wb, ws: None, name="resaved.xlsx")
    spent = parse_excel(str(path))["spent_time_roles"]
    assert [r["task_id"] for r in spent] == ["SRV-4041", "SRV-4011", "DB-202",
                                              "MP-102", "MOB-7011", "KP-3898"]


def test_tail_sp_is_the_same_on_week0_and_after_first_plan():
    """У задачи с уже потраченными часами и ролью без людей остаток SP на Неделе 0
    и после первого плана (когда появился unstaffed_hh) один и тот же — иначе план
    сдвигается при пересчёте без всяких отклонений."""
    from backend.recalculation.plan_fields import normalize_role
    from backend.recalculation.recalc import _tail_input

    task = {"task_id": "T", "status": "ToDo", "estimation_sp": 5,
            "estimated_hh": {"Аналитик": 40, "Руководитель проекта": 10},
            "spent_hh": {"Аналитик": 20}}
    roles = {normalize_role("Аналитик")}
    week0 = _tail_input(task, roles)["estimation_sp"]
    after = _tail_input({**task, "unstaffed_hh": {"Руководитель проекта": 10}}, roles)["estimation_sp"]
    assert week0 == after == 2.5
