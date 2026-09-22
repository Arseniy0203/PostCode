import os
import pandas as pd
import warnings

warnings.filterwarnings('ignore')


def find_row(df, keyword):
    mask = df[0].astype(str).str.contains(keyword, na=False, regex=False)
    if mask.any():
        return df[mask].index[0]
    raise ValueError(f"Не найден маркер: {keyword}")


def apply_modifications(master_df, mods):
    """
    mods: dict вида {
        task_id: {"status": "Done", "spent": {"Разработчик Java": 20}}
    }
    """
    df = master_df.copy()

    try:
        idx_tasks = find_row(df, 'Tasks (Таблица задач и скоринга)')
        idx_spent = find_row(df, 'Spent_time_roles')
        idx_estimates = find_row(df, 'Estimates. Калькулятор сметы')
    except ValueError as e:
        print(f"Ошибка поиска маркеров: {e}")
        return df

    # 1. Обновляем блок Tasks
    headers_tasks = df.iloc[idx_tasks + 1].astype(str).str.strip()
    if 'task_id' in headers_tasks.values and 'status' in headers_tasks.values:
        col_tid = headers_tasks[headers_tasks == 'task_id'].index[0]
        col_status = headers_tasks[headers_tasks == 'status'].index[0]

        for i in range(idx_tasks + 2, idx_spent):
            tid = str(df.iloc[i, col_tid]).strip()
            if tid in mods and "status" in mods[tid]:
                df.iloc[i, col_status] = mods[tid]["status"]

    # 2. Обновляем блок Spent_time_roles
    headers_spent = df.iloc[idx_spent + 1].astype(str).str.strip()
    if 'task_id' in headers_spent.values:
        col_spent_tid = headers_spent[headers_spent == 'task_id'].index[0]

        for i in range(idx_spent + 2, idx_estimates):
            tid = str(df.iloc[i, col_spent_tid]).strip()
            if tid in mods and "spent" in mods[tid]:
                for role, hours in mods[tid]["spent"].items():
                    # Ищем колонку роли
                    role_cols = headers_spent[headers_spent == role].index
                    if len(role_cols) > 0:
                        r_col = role_cols[0]
                        current_val = pd.to_numeric(df.iloc[i, r_col], errors='coerce')
                        current_val = 0 if pd.isna(current_val) else current_val
                        df.iloc[i, r_col] = current_val + hours

    return df


def generate_scenarios():
    master_path = "Датасет_с_правками_основной.xlsx"
    if not os.path.exists(master_path):
        master_path = os.path.join("data", "raw", "Датасет_с_правками_основной.xlsx")

    if not os.path.exists(master_path):
        print(f"КРИТИЧЕСКАЯ ОШИБКА: Не найден файл {master_path}")
        return

    print("Читаем мастер-датасет...")
    master_df = pd.read_excel(master_path, header=None)

    scenarios = {
        "Scenario_1_Ideal": [
            # Спринт 1
            {"SRV-4011": {"status": "Done",
                          "spent": {"Аналитик": 40, "Разработчик Java": 40, "Тестировщик (Автотестирование)": 10}}},
            # Спринт 2
            {"ONK-2914": {"status": "InProgress", "spent": {"Разработчик Java": 40}}},
            # Спринты 3-6 (копим прогресс)
            {"ONK-2914": {"status": "Done", "spent": {"Разработчик Java": 80, "Аналитик": 16}}},
            {}, {}, {}
        ],
        "Scenario_2_Cascade_Delay": [
            # Спринт 1: DB-202 (огромная задача на 130ч .NET) почти не двигается
            {"DB-202": {"status": "InProgress", "spent": {"Разработчик .net": 10}}},
            # Спринт 2: Все еще буксуем
            {"DB-202": {"status": "InProgress", "spent": {"Разработчик .net": 10}}},
            # Спринт 3
            {"DB-202": {"status": "InProgress", "spent": {"Разработчик .net": 10}}},
            {}, {}, {}
        ],
        "Scenario_3_Canceled_Task": [
            # Спринт 1: Заказчик внезапно отменяет крупную инициативу
            {"SRV-4041": {"status": "Cancelled", "spent": {}}},
            # Спринт 2
            {"SRV-4011": {"status": "InProgress", "spent": {"Аналитик": 20}}},
            {}, {}, {}, {}
        ]
    }

    base_out_dir = "demo_scenarios"
    os.makedirs(base_out_dir, exist_ok=True)

    for sc_name, sprints_mods in scenarios.items():
        sc_dir = os.path.join(base_out_dir, sc_name)
        os.makedirs(sc_dir, exist_ok=True)

        # Накопительный словарь изменений
        cumulative_mods = {}

        for sprint_idx, mods in enumerate(sprints_mods, 1):
            # Добавляем новые изменения к накопленным
            for tid, changes in mods.items():
                if tid not in cumulative_mods:
                    cumulative_mods[tid] = {"status": "", "spent": {}}

                if "status" in changes:
                    cumulative_mods[tid]["status"] = changes["status"]

                if "spent" in changes:
                    for role, hours in changes["spent"].items():
                        cumulative_mods[tid]["spent"][role] = cumulative_mods[tid]["spent"].get(role, 0) + hours

            out_df = apply_modifications(master_df, cumulative_mods)
            out_file = os.path.join(sc_dir, f"Sprint_{sprint_idx}_Fact.xlsx")
            out_df.to_excel(out_file, index=False, header=False)

        print(f"Сгенерирован сценарий: {sc_name}")

    print("\nГенерация завершена. Ищи файлы в папке 'demo_scenarios'.")


if __name__ == "__main__":
    generate_scenarios()
