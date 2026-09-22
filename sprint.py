"""Генератор демо-фактов спринтов в формате исходного датасета (папка demo_scenarios).

Каждый файл Sprint_N_Fact.xlsx — копия актуального Excel-датасета, в которой
обновлены статусы задач (Tasks) и НАКОПЛЕННЫЕ часы по ролям (Spent_time_roles)
на конец спринта N. Интерфейс загружает такой файл как факт спринта;
backend.recalculation.pipeline.fact_from_dataset_excel вычитает уже учтённое
и получает часы именно за этот спринт.

Факты берутся из симуляции по нашему же плану (backend/recalculation/scenarios.py),
поэтому «идеальный» сценарий действительно идёт по плану, а отклонения видны
ровно там, где их задумали:
    Scenario_1_Ideal          — всё по плану (on_track);
    Scenario_2_Cascade_Delay  — задержка ключевой задачи в спринте 1 (key_task_delay);
    Scenario_3_Canceled_Task  — по плану, но в спринте 1 заказчик отменяет SRV-4041.

Запуск из корня репозитория:  python sprint.py
"""

from __future__ import annotations

import copy
import os
import warnings

import pandas as pd

from backend.recalculation.pipeline import run_quarter
from backend.recalculation.plan_fields import normalize_role

warnings.filterwarnings("ignore")

MASTER = os.path.join("data", "raw", "Хакатон_датасетс_правками_по_списку_вопросов_1.xlsx")
OUT_DIR = "demo_scenarios"
CANCELLED_TASK = "SRV-4041"


def find_row(df: pd.DataFrame, keyword: str) -> int:
    mask = df[0].astype(str).str.contains(keyword, na=False, regex=False)
    if mask.any():
        return int(df[mask].index[0])
    raise ValueError(f"Не найден маркер: {keyword}")


def _header(df: pd.DataFrame, start: int, end: int) -> tuple[int, dict[str, int]]:
    """Строка заголовка таблицы (самая заполненная) и колонки по имени."""
    block = df.iloc[start + 1:end]
    row = int(block.notna().sum(axis=1).idxmax())
    columns = {str(v).strip(): int(c) for c, v in df.iloc[row].items() if pd.notna(v)}
    return row, columns


def write_fact(master: pd.DataFrame, statuses: dict[str, str],
               spent: dict[str, dict[str, float]], estimates: dict[str, float]) -> pd.DataFrame:
    """Копия датасета со статусами и накопленными часами (новые строки — для задач без факта)."""
    df = master.copy()
    idx_tasks = find_row(df, "Tasks (Таблица задач и скоринга)")
    idx_spent = find_row(df, "Spent_time_roles")
    idx_est = find_row(df, "Estimates. Калькулятор сметы")

    head, cols = _header(df, idx_tasks, idx_spent)
    for i in range(head + 1, idx_spent):
        tid = str(df.iat[i, cols["task_id"]]).strip()
        if tid in statuses:
            df.iat[i, cols["status"]] = statuses[tid]

    head, cols = _header(df, idx_spent, idx_est)
    role_col = {normalize_role(name): c for name, c in cols.items()
                if name not in ("task_id", "Исходное время")}
    seen, last = set(), head
    for i in range(head + 1, idx_est):
        tid = str(df.iat[i, cols["task_id"]]).strip()
        if tid in ("", "nan", "None"):
            continue
        last = i
        seen.add(tid)
        for role, hours in spent.get(tid, {}).items():
            if normalize_role(role) in role_col:
                df.iat[i, role_col[normalize_role(role)]] = round(hours, 2)

    new_rows = []
    for tid in sorted(set(spent) - seen):
        row = [None] * df.shape[1]
        row[cols["task_id"]] = tid
        if "Исходное время" in cols:
            row[cols["Исходное время"]] = estimates.get(tid)
        for c in role_col.values():
            row[c] = 0
        for role, hours in spent[tid].items():
            if normalize_role(role) in role_col:
                row[role_col[normalize_role(role)]] = round(hours, 2)
        new_rows.append(row)
    if new_rows:
        extra = pd.DataFrame(new_rows, columns=df.columns)
        df = pd.concat([df.iloc[:last + 1], extra, df.iloc[last + 1:]], ignore_index=True)
    return df


def scenario_facts(name: str) -> list[dict]:
    if name == "Scenario_1_Ideal":
        report = run_quarter("on_track", 6, with_hiring=False)
    elif name == "Scenario_2_Cascade_Delay":
        report = run_quarter("key_task_delay", 6, with_hiring=False)
    else:
        report = run_quarter("on_track", 6, with_hiring=False)
    facts = [copy.deepcopy((h.get("fact") or {}).get("tasks") or {}) for h in report["history"][1:]]
    if name == "Scenario_3_Canceled_Task":
        facts[0][CANCELLED_TASK] = {"status": "Cancelled", "spent_hh": {}}
        for later in facts[1:]:
            later.pop(CANCELLED_TASK, None)
    return facts


def generate_scenarios() -> None:
    if not os.path.exists(MASTER):
        raise SystemExit(f"Не найден датасет {MASTER}")
    master = pd.read_excel(MASTER, header=None)
    base = run_quarter(until_sprint=0, with_hiring=False)
    current = {t["task_id"]: t for t in base["plan"]["current"]}
    estimates = {tid: sum(float(h) for h in (t.get("estimated_hh") or {}).values())
                 for tid, t in current.items() if isinstance(t.get("estimated_hh"), dict)}

    for name in ("Scenario_1_Ideal", "Scenario_2_Cascade_Delay", "Scenario_3_Canceled_Task"):
        os.makedirs(os.path.join(OUT_DIR, name), exist_ok=True)
        statuses = {tid: t.get("status") for tid, t in current.items()}
        spent = {tid: dict(t.get("spent_hh") or {}) for tid, t in current.items() if t.get("spent_hh")}
        for sprint, fact in enumerate(scenario_facts(name), 1):
            for tid, entry in fact.items():
                if entry.get("status"):
                    statuses[tid] = entry["status"]
                acc = spent.setdefault(tid, {})
                for role, hours in (entry.get("spent_hh") or {}).items():
                    key = next((r for r in acc if normalize_role(r) == normalize_role(role)), role)
                    acc[key] = acc.get(key, 0.0) + float(hours)
            out = write_fact(master, statuses, spent, estimates)
            out.to_excel(os.path.join(OUT_DIR, name, f"Sprint_{sprint}_Fact.xlsx"),
                         index=False, header=False)
        print(f"Сгенерирован сценарий: {name}")
    print(f"Готово: {OUT_DIR}/")


if __name__ == "__main__":
    generate_scenarios()
