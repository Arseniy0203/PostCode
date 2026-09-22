from __future__ import annotations

import json
import re
import os
import sys
import pandas as pd
from typing import Any


TASKS_MARKER = 'Tasks (Таблица задач и скоринга)'


def parse_excel(filepath: str) -> dict[str, Any]:
    """Парсит Excel-датасет в словарь из 6 сущностей."""

    # Файл закрываем сразу: на Windows открытый ExcelFile не даёт удалить временный файл.
    try:
        xls_file = pd.ExcelFile(filepath)
    except Exception as exc:  # noqa: BLE001 — не Excel: понятная ошибка вместо трассировки pandas
        raise ValueError("Файл не читается как Excel (.xlsx). Возможно, это другой формат "
                         "с расширением .xlsx или файл повреждён.") from exc
    with xls_file as xls:
        # Лист с данными — тот, где есть таблица задач: имя листа и лишние листы
        # впереди (инструкция, титул) в скрытом датасете могут отличаться.
        preferred = 'Датасет(обновленный)'
        names = [preferred] if preferred in xls.sheet_names else []
        names += [n for n in xls.sheet_names if n != preferred]
        df, sheet_name = None, None
        for name in names:
            sheet = pd.read_excel(xls, sheet_name=name, header=None)
            if not sheet.empty and sheet[0].astype(str).str.contains(
                    TASKS_MARKER, na=False, regex=False).any():
                df, sheet_name = sheet, name
                break
    if df is None:
        raise ValueError("Файл не похож на датасет: ни на одном листе нет таблицы "
                         f"«{TASKS_MARKER}». Загрузите Excel в формате датасета организаторов.")

    df = _fill_formula_refs(filepath, sheet_name, df)

    def find_row(keyword: str, required: bool = True) -> int | None:
        mask = df[0].astype(str).str.contains(keyword, na=False, regex=False)
        if mask.any():
            return df[mask].index[0]
        if required:
            raise ValueError(f"В файле нет таблицы «{keyword}» — без неё план не построить.")
        return None

    idx_tasks = find_row(TASKS_MARKER)
    # Необязательные таблицы: без них — пустой список (и отчёт о качестве данных), а не отказ
    idx_spent = find_row('Spent_time_roles', required=False)
    idx_estimates = find_row('Estimates. Калькулятор сметы')
    idx_deps = find_row('Task_dependencies', required=False)
    idx_engineers = find_row('Engineers_profiles')
    idx_history = find_row('Team_history')
    idx_dictionary = find_row('Итог разработки', required=False)

    # Конец таблицы — ближайший следующий маркер (любой), а не «следующий по
    # порядку»: если таблицы в файле переставить, срезы не перепутаются.
    markers = sorted({i for i in (idx_tasks, idx_spent, idx_estimates, idx_deps, idx_engineers,
                                  idx_history, idx_dictionary) if i is not None})

    def end_of(start: int | None) -> int:
        if start is None:
            return 0
        return next((m for m in markers if m > start), len(df))

    def slice_to_dict_robust(start_idx: int | None, end_idx: int) -> list[dict[str, Any]]:
        if start_idx is None:
            return []
        raw_slice = df.iloc[start_idx + 1 : end_idx].copy()
        raw_slice = raw_slice.dropna(how='all', axis=0).dropna(how='all', axis=1)
        if raw_slice.empty:
            return []

        header_idx = raw_slice.notna().sum(axis=1).idxmax()

        raw_slice.columns = raw_slice.loc[header_idx].astype(str).str.strip().str.replace('\n', ' ')
        clean_slice = raw_slice.loc[header_idx + 1:].reset_index(drop=True)

        clean_slice = clean_slice.dropna(how='all', axis=0)
        clean_slice = clean_slice.where(pd.notnull(clean_slice), None)

        return clean_slice.to_dict(orient='records')

    tasks = slice_to_dict_robust(idx_tasks, end_of(idx_tasks))
    spent_time = slice_to_dict_robust(idx_spent, end_of(idx_spent))
    # Часы по ролям — числа, в том числе «12,5».
    for row in spent_time:
        for key, value in row.items():
            if key not in ('task_id', 'Исходное время') and value is not None:
                num = _number(value)
                # нечисловой факт («—», «н/д») — не потрачено, а не падение расчёта
                row[key] = float(num) if isinstance(num, (int, float)) else 0.0
    history = slice_to_dict_robust(idx_history, end_of(idx_history))

    engineers = slice_to_dict_robust(idx_engineers, end_of(idx_engineers))
    _require(tasks, "Tasks", ("task_id", "team_id", "status", "estimation_sp"))
    _require(engineers, "Engineers_profiles", ("engineer_id", "team_id", "role", "capacity_rate"))
    _require(history, "Team_history", ("team_id", "velocity_achieved"))
    for eng in engineers:
        rate = _number(eng.get('capacity_rate'))
        # пустая или нечисловая доля — полная занятость (1.0), как у большинства в датасете
        eng['capacity_rate'] = rate if isinstance(rate, (int, float)) and rate > 0 else 1.0
    for row in history:
        for key in ('velocity_achieved', 'planned_sp'):
            if key in row:
                value = _number(row.get(key))
                row[key] = value if isinstance(value, (int, float)) else None
    for eng in engineers:
        raw_skills = str(eng.get('skills_declared', ''))
        if raw_skills and raw_skills != 'None':
            eng['skills_declared'] = [s.strip() for s in re.split(r',\s*(?![^()]*\))', raw_skills) if s.strip()]
        else:
            eng['skills_declared'] = []

    # ==========================================
    # РУЧНОЙ ПАРСИНГ ЗАВИСИМОСТЕЙ (БЕЗ УМНОГО ПОИСКА)
    # ==========================================
    raw_deps = (df.iloc[idx_deps + 1 : end_of(idx_deps)].copy() if idx_deps is not None
                else pd.DataFrame())
    raw_deps = raw_deps.dropna(how='all', axis=0)
    dependencies = []
    if not raw_deps.empty:
        # Жестко берем первые 3 колонки (A, B, C)
        deps_clean = raw_deps.iloc[:, 0:3].copy()
        # Шапку (в датасете она съехала) отбрасываем по содержимому, а не «первую строку»:
        # если шапку в скрытом датасете починят, ни одна связь не потеряется.
        # Принудительно задаем имена колонкам
        deps_clean.columns = ['blocking_task_id', 'blocked_task_id', 'dependency_type']
        is_header = deps_clean['blocking_task_id'].astype(str).str.contains(
            'blocking|blocked|task', case=False, na=False)
        deps_clean = deps_clean[~is_header]

        # Вычищаем полностью пустые строки
        deps_clean = deps_clean.dropna(how='any')
        # Дубли (например, SRV-4015 -> SRV-4016 с разными типами связи) не теряем
        # молча: первая связь остаётся, повтор уходит в лог исключённых.
        dup_mask = deps_clean.duplicated(subset=['blocking_task_id', 'blocked_task_id'], keep='first')
        duplicates = deps_clean[dup_mask].to_dict(orient='records')
        deps_clean = deps_clean[~dup_mask]

        dependencies = deps_clean.to_dict(orient='records')

    # Транспонирование Estimates
    est_raw = slice_to_dict_robust(idx_estimates, end_of(idx_estimates))
    estimates = []
    if est_raw:
        est_df = pd.DataFrame(est_raw)
        if 'Роль' in est_df.columns:
            melted = est_df.melt(
                id_vars=['Роль'],
                var_name='task_id',
                value_name='hours'
            )
            # «130,5» из русской локали Excel — это 130.5, а не пропуск.
            melted['hours'] = pd.to_numeric(
                melted['hours'].astype(str).str.replace(',', '.', regex=False).str.strip(),
                errors='coerce').fillna(0)
            melted = melted[melted['hours'] > 0]
            melted = melted.rename(columns={'Роль': 'role'})

            melted = melted.where(pd.notnull(melted), None)
            estimates = melted.to_dict(orient='records')

    for task in tasks:
        sp = _number(task.get('estimation_sp'))
        # «пять», «—» и пустое — нет оценки в SP (задача попадёт в отчёт о качестве данных)
        task['estimation_sp'] = sp if isinstance(sp, (int, float)) and sp == sp else None

    return {
        "tasks": tasks,
        "estimates": estimates,
        "spent_time_roles": spent_time,
        "task_dependencies": dependencies,
        "engineers_profiles": engineers,
        "team_history": history,
        "duplicate_dependencies": duplicates if not raw_deps.empty else [],
    }


_CELL_REF = re.compile(r"^=\s*\$?([A-Za-z]{1,3})\$?(\d+)\s*$")


def _fill_formula_refs(filepath: Any, sheet_name: str, df: pd.DataFrame) -> pd.DataFrame:
    """Подставляет значения формул-ссылок (=B24), если в файле нет их кэша.

    В датасете организаторов ID задач в Spent_time_roles — формулы «=B24». Excel
    хранит рядом вычисленное значение, и pandas читает его. Но файл, сохранённый
    программой без пересчёта (openpyxl, часть онлайн-редакторов), хранит только
    формулу — pandas видит пустую ячейку, и факт по задачам в работе теряется.
    Простые ссылки на ячейку того же листа вычисляем сами; прочие формулы не трогаем.
    """
    try:
        from openpyxl import load_workbook
        from openpyxl.utils import column_index_from_string
    except ImportError:
        return df

    def load(values_only: bool):
        if hasattr(filepath, "seek"):
            filepath.seek(0)
        return load_workbook(filepath, data_only=values_only, read_only=False)

    try:
        ws_formula = load(False)[sheet_name]
        formulas = {c.coordinate: c.value for row in ws_formula.iter_rows() for c in row
                    if isinstance(c.value, str) and c.value.startswith("=")}
        if not formulas:
            return df
        ws_cached = load(True)[sheet_name]
    except Exception:  # noqa: BLE001 — запасной путь: не смогли — оставляем как прочитал pandas
        return df

    def resolve(coord: str, depth: int = 0) -> Any:
        cached = ws_cached[coord].value
        if cached is not None or coord not in formulas:
            return cached if cached is not None else ws_formula[coord].value
        match = _CELL_REF.match(formulas[coord])
        if not match or depth > 20:
            return None
        return resolve(f"{match.group(1).upper()}{match.group(2)}", depth + 1)

    # pandas читает лист начиная с первой непустой строки/колонки
    row_offset, col_offset = ws_formula.min_row, ws_formula.min_column
    df = df.copy()
    for coord in formulas:
        if ws_cached[coord].value is not None:
            continue
        value = resolve(coord)
        if value is None:
            continue
        cell = ws_formula[coord]
        r, c = cell.row - row_offset, cell.column - col_offset
        if 0 <= r < len(df) and 0 <= c < len(df.columns):
            df.iat[r, c] = value
    return df


def _number(value: Any) -> Any:
    """'0,5' → 0.5, '5' → 5.0; пустое и нечисловое остаётся как есть."""
    if value is None or isinstance(value, (int, float)):
        return value
    text = str(value).replace(',', '.').strip()
    try:
        return float(text)
    except ValueError:
        return value


def _require(rows: list[dict[str, Any]], table: str, columns: tuple[str, ...]) -> None:
    """Таблица без обязательных колонок — ошибка сразу, а не битые данные дальше."""
    if not rows:
        raise ValueError(f"Таблица {table} пуста или не найдена")
    missing = [c for c in columns if c not in rows[0]]
    if missing:
        raise ValueError(f"В таблице {table} нет колонок: {', '.join(missing)} "
                         f"(найдены: {', '.join(map(str, rows[0]))})")


def filter_orphan_dependencies(
    deps: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    valid_task_ids = {str(t.get('task_id')) for t in tasks if t.get('task_id') is not None}

    clean_deps = []
    excluded_log = []

    for dep in deps:
        blocking = str(dep.get('blocking_task_id', ''))
        blocked = str(dep.get('blocked_task_id', ''))

        if blocking not in valid_task_ids:
            excluded_log.append({
                "dependency": dep,
                "reason": f"blocking_task_id '{blocking}' не найден в Tasks"
            })
        elif blocked not in valid_task_ids:
            excluded_log.append({
                "dependency": dep,
                "reason": f"blocked_task_id '{blocked}' не найден в Tasks"
            })
        else:
            clean_deps.append(dep)

    return clean_deps, excluded_log


if __name__ == "__main__":
    raw_dir = os.path.join("data", "raw")
    processed_dir = os.path.join("data", "processed")

    # Актуальная версия датасета от организаторов (правки по списку вопросов);
    # путь можно передать аргументом: python -m backend.ingestion.parse_dataset <file.xlsx>
    expected_filepath = os.path.join(raw_dir, "Хакатон_датасетс_правками_по_списку_вопросов_1.xlsx")
    input_filepath = sys.argv[1] if len(sys.argv) > 1 else expected_filepath

    if not os.path.exists(input_filepath):
        if os.path.exists(raw_dir):
            xlsx_files = [f for f in os.listdir(raw_dir) if f.endswith('.xlsx') and not f.startswith('~')]
            if xlsx_files:
                input_filepath = os.path.join(raw_dir, xlsx_files[0])
                print(f"ВНИМАНИЕ: Точный файл не найден. Берем первый доступный Excel: {input_filepath}")
            else:
                print(f"КРИТИЧЕСКАЯ ОШИБКА: В папке {raw_dir} нет ни одного файла .xlsx")
                exit(1)
        else:
            print(f"КРИТИЧЕСКАЯ ОШИБКА: Папка {raw_dir} не существует. Запустите скрипт из корня проекта.")
            exit(1)

    print(f"Начинаем парсинг файла: {input_filepath}")

    try:
        parsed_data = parse_excel(input_filepath)
        print("Парсинг Excel успешно завершен.")

        clean_deps, exclude_log = filter_orphan_dependencies(
            parsed_data["task_dependencies"],
            parsed_data["tasks"]
        )
        exclude_log.extend({"dependency": dep, "reason": "дубликат связи"}
                           for dep in parsed_data.pop("duplicate_dependencies", []))

        parsed_data["task_dependencies"] = clean_deps
        print(f"Фильтрация графа: оставлено {len(clean_deps)} связей, удалено {len(exclude_log)}")

        os.makedirs(processed_dir, exist_ok=True)

        for key, data_list in parsed_data.items():
            out_file = os.path.join(processed_dir, f"{key}.json")
            with open(out_file, 'w', encoding='utf-8') as f:
                json.dump(data_list, f, ensure_ascii=False, indent=4, default=str)
            print(f"Сохранено: {out_file}")

        # Лог пишем всегда (в том числе пустой): иначе от прошлой версии датасета
        # останутся связи, которых в новой уже нет.
        if True:
            log_file = os.path.join(processed_dir, "excluded_dependencies_log.json")
            with open(log_file, 'w', encoding='utf-8') as f:
                json.dump(exclude_log, f, ensure_ascii=False, indent=4, default=str)
            print(f"Лог удаленных зависимостей сохранен: {log_file}")

        print(f"\nETL процесс завершен! Все 6 таблиц лежат в {processed_dir}")

    except Exception as e:
        print(f"Критическая ошибка при выполнении ETL: {e}")