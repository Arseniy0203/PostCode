from __future__ import annotations

import json
import re
import os
import pandas as pd
from typing import Any


def parse_excel(filepath: str) -> dict[str, Any]:
    xls = pd.ExcelFile(filepath)
    sheet_name = 'Датасет(обновленный)'
    if sheet_name not in xls.sheet_names:
        sheet_name = xls.sheet_names[0] 
        
    df = pd.read_excel(filepath, sheet_name=sheet_name, header=None)

    def find_row(keyword: str) -> int:
        mask = df[0].astype(str).str.contains(keyword, na=False, regex=False)
        if mask.any():
            return df[mask].index[0]
        raise ValueError(f"Не найден маркер таблицы: '{keyword}'")

    idx_tasks = find_row('Tasks (Таблица задач и скоринга)')
    idx_spent = find_row('Spent_time_roles')
    idx_estimates = find_row('Estimates. Калькулятор сметы')
    idx_deps = find_row('Task_dependencies')
    idx_engineers = find_row('Engineers_profiles')
    idx_history = find_row('Team_history')
    idx_dictionary = find_row('Итог разработки')

    def slice_to_dict_robust(start_idx: int, end_idx: int) -> list[dict[str, Any]]:
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

    tasks = slice_to_dict_robust(idx_tasks, idx_spent)
    
    # Лечим запятые в часах прямо на входе (Spent_time_roles)
    spent_time = slice_to_dict_robust(idx_spent, idx_estimates)
    for st in spent_time:
        for k, v in st.items():
            if k not in ('task_id', 'Исходное время') and v is not None:
                try:
                    st[k] = float(str(v).replace(',', '.'))
                except ValueError:
                    pass

    history = slice_to_dict_robust(idx_history, idx_dictionary)
    
    engineers = slice_to_dict_robust(idx_engineers, idx_history)
    for eng in engineers:
        raw_skills = str(eng.get('skills_declared', ''))
        if raw_skills and raw_skills != 'None':
            eng['skills_declared'] = [s.strip() for s in re.split(r',\s*(?![^()]*\))', raw_skills) if s.strip()]
        else:
            eng['skills_declared'] = []

  
    raw_deps = df.iloc[idx_deps + 1 : idx_engineers].copy()
    raw_deps = raw_deps.dropna(how='all', axis=0)
    dependencies = []
    if not raw_deps.empty:
        deps_clean = raw_deps.iloc[:, 0:3].copy()
        deps_clean.columns = ['blocking_task_id', 'blocked_task_id', 'dependency_type']
        
        # Выкидываем строки, которые выглядят как шапка (чтобы не потерять данные, если шапку починят)
        mask_header = deps_clean['blocking_task_id'].astype(str).str.contains('blocking|blocked|task', case=False, na=False)
        deps_clean = deps_clean[~mask_header]
        
        deps_clean = deps_clean.dropna(how='any')
        deps_clean = deps_clean.drop_duplicates(subset=['blocking_task_id', 'blocked_task_id'], keep='first')
        dependencies = deps_clean.to_dict(orient='records')

    # Транспонирование Estimates с обработкой запятых
    est_raw = slice_to_dict_robust(idx_estimates, idx_deps)
    estimates = []
    if est_raw:
        est_df = pd.DataFrame(est_raw)
        if 'Роль' in est_df.columns:
            melted = est_df.melt(
                id_vars=['Роль'],
                var_name='task_id',
                value_name='hours'
            )
            # Замена запятых на точки перед кастом в float
            melted['hours'] = pd.to_numeric(
                melted['hours'].astype(str).str.replace(',', '.'), 
                errors='coerce'
            ).fillna(0)
            
            melted = melted[melted['hours'] > 0]
            melted = melted.rename(columns={'Роль': 'role'})
            
            melted = melted.where(pd.notnull(melted), None)
            estimates = melted.to_dict(orient='records')

    return {
        "tasks": tasks,
        "estimates": estimates,
        "spent_time_roles": spent_time,
        "task_dependencies": dependencies,
        "engineers_profiles": engineers,
        "team_history": history
    }


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
    
    expected_filepath = os.path.join(raw_dir, "Датасет_с_правками_основной.xlsx")
    input_filepath = expected_filepath

    if not os.path.exists(input_filepath):
        if os.path.exists(raw_dir):
            xlsx_files = [f for f in os.listdir(raw_dir) if f.endswith('.xlsx') and not f.startswith('~')]
            if xlsx_files:
                input_filepath = os.path.join(raw_dir, xlsx_files[0])
            else:
                print(f"КРИТИЧЕСКАЯ ОШИБКА: В папке {raw_dir} нет ни одного файла .xlsx")
                exit(1)
        else:
            print(f"КРИТИЧЕСКАЯ ОШИБКА: Папка {raw_dir} не существует.")
            exit(1)
            
    try:
        parsed_data = parse_excel(input_filepath)
        clean_deps, exclude_log = filter_orphan_dependencies(
            parsed_data["task_dependencies"], 
            parsed_data["tasks"]
        )
        parsed_data["task_dependencies"] = clean_deps
        
        os.makedirs(processed_dir, exist_ok=True)
        for key, data_list in parsed_data.items():
            out_file = os.path.join(processed_dir, f"{key}.json")
            with open(out_file, 'w', encoding='utf-8') as f:
                json.dump(data_list, f, ensure_ascii=False, indent=4, default=str)

        if exclude_log:
            log_file = os.path.join(processed_dir, "excluded_dependencies_log.json")
            with open(log_file, 'w', encoding='utf-8') as f:
                json.dump(exclude_log, f, ensure_ascii=False, indent=4, default=str)
                
        print(f"ETL процесс завершен! Все 6 таблиц лежат в {processed_dir}")
    except Exception as e:
        print(f"Критическая ошибка при выполнении ETL: {e}")