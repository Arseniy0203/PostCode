# Ответственный: Человек 4 (frontend + tests + docs)
"""
Точка входа Streamlit-приложения сервиса PI-планирования.

Навигация между страницами через sidebar, поддержка режимов Baseline/Current
и безопасная загрузка данных.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import streamlit as st

from views.plan_view import render_plan_view
from views.risk_view import render_risk_view
from views.star_map_view import render_star_map_view
from backend.kpi.metrics import kpi_breakdown

# Константы конфигурации
DEFAULT_DATA_PATH = os.path.join("data", "processed", "tasks.json")
DEFAULT_ENGINEERS_PATH = os.path.join("data", "processed", "engineers_profiles.json")

DATA_PATH = os.getenv("APP_DATA_PATH", DEFAULT_DATA_PATH)
ENGINEERS_PATH = os.getenv("ENGINEERS_DATA_PATH", DEFAULT_ENGINEERS_PATH)
APP_TITLE = os.getenv("APP_TITLE", "ПочтаТех: PI-Планирование")
LOGO_PATH = os.getenv("LOGO_PATH", os.path.join("frontend", "assets", "logo_pochtatex.png"))


def inject_custom_css() -> None:
    """
    Инжектирует CSS для стилизации интерфейса в стиле Glassmorphism (Liquid Glass).
    Форсирует темный цвет текста для предотвращения слияния со светлым фоном
    в случае активного Dark Mode в браузере/системе.
    """
    st.markdown(
        """
        <style>
        /* Скрытие стандартного хедера, футера и гамбургер-меню */
        #MainMenu {visibility: hidden;}
        header {visibility: hidden;}
        footer {visibility: hidden;}

        /* Корпоративный фон (мягкий градиент от светло-синего к белому) */
        .stApp {
            background: linear-gradient(135deg, #e3f2fd 0%, #ffffff 100%);
            background-attachment: fixed;
        }

        /* =========================================
           ФОРСИРОВАНИЕ ТЕМНОГО ТЕКСТА (Анти-DarkMode) 
           ========================================= */
        .stApp, .stApp p, .stApp span, .stApp label, .stApp li, .stApp div {
            color: #1e293b !important; /* Глубокий серо-синий для основного текста */
        }

        h1, h2, h3, h4, h5, h6 {
            color: #003366 !important; /* Корпоративный синий ПочтаТеха */
        }

        /* Заголовки экспандеров, вкладки (tabs) и значения метрик */
        .streamlit-expanderHeader, button[data-baseweb="tab"] p, div[data-testid="stMetricValue"] > div {
            color: #003366 !important;
        }
        /* ========================================= */

        /* ========================================= */

        /* Стилизация Glassmorphism для карточек (только метрики, экспандеры и контейнеры с рамкой) */
        div[data-testid="stMetric"],
        div[data-testid="stExpander"],
        div[data-testid="stVerticalBlockBorderWrapper"] {
            background: rgba(255, 255, 255, 0.7) !important;
            backdrop-filter: blur(12px) !important;
            -webkit-backdrop-filter: blur(12px) !important;
            border: 1px solid rgba(255, 255, 255, 0.8) !important;
            border-radius: 16px !important;
            box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.07) !important;
            padding: 1rem;
            transition: transform 0.2s ease, box-shadow 0.2s ease;
        }

        /* Анимация при наведении */
        div[data-testid="stMetric"]:hover {
            transform: translateY(-3px);
            box-shadow: 0 12px 40px 0 rgba(31, 38, 135, 0.12) !important;
        }

        /* Полупрозрачный сайдбар с темным текстом */
        [data-testid="stSidebar"] {
            background: rgba(255, 255, 255, 0.5) !important;
            backdrop-filter: blur(15px);
            border-right: 1px solid rgba(255, 255, 255, 0.6);
        }

        [data-testid="stSidebar"] p, [data-testid="stSidebar"] span, [data-testid="stSidebar"] label {
            color: #1e293b !important;
        }
        </style>
        """,
        unsafe_allow_html=True
    )


@st.cache_data
def load_data(filepath: str) -> list[dict[str, Any]]:
    """
    Загружает данные задач из JSON файла.

    В случае отсутствия файла возвращает пустой список, чтобы UI не падал.

    Args:
        filepath: Путь к JSON файлу с данными.

    Returns:
        Список словарей с данными задач.
    """
    if not os.path.exists(filepath):
        st.error(f"Файл данных не найден: {filepath}")
        return []

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Адаптация сырых данных из tasks.json под схему PlannedTask для демо
    # В реальном приложении это делает backend/planning/scheduler.py
    for i, task in enumerate(data):
        if "decision" not in task:
            # Мокируем решения для демонстрации UI
            if task.get("status") == "Done":
                task["decision"] = "included"
                task["planned_start"] = 1
                task["planned_end"] = 2
                task["reason_code"] = "ALREADY_IN_PROGRESS"
                task["reason_detail"] = "Задача уже завершена или в работе, план продолжает её выполнение."
                task["reason_facts"] = {"task_id": task["task_id"]}
            elif i % 5 == 0:
                task["decision"] = "cancelled"
                task["planned_start"] = None
                task["planned_end"] = None
                task["reason_code"] = "REQUIREMENTS_CHANGED"
                task["reason_detail"] = "Изменились требования: заказчик исключил интеграцию."
                task["reason_facts"] = {"task_id": task["task_id"], "note": "заказчик исключил интеграцию"}
            elif i % 4 == 0:
                task["decision"] = "postponed"
                task["planned_start"] = None
                task["planned_end"] = None
                task["reason_code"] = "NO_RESOURCES"
                task[
                    "reason_detail"] = ("Не хватает 50 ч роли «Разработчик .net» в спринте 2: "
                                        "нужно 130 ч, доступно 80 ч.")
                task["reason_facts"] = {"task_id": task["task_id"], "role": "Разработчик .net", "need_hh": 130,
                                        "available_hh": 80}
            else:
                task["decision"] = "included"
                task["planned_start"] = (i % 6) + 1
                task["planned_end"] = min(((i % 6) + 1 + int(task.get("estimation_sp", 1) // 4)), 6)
                task["reason_code"] = "FITS_CAPACITY"
                task[
                    "reason_detail"] = (f"Поставлена в спринт {task['planned_start']}: "
                                        f"команда {task.get('team_id')} укладывается в ёмкость.")
                task["reason_facts"] = {"task_id": task["task_id"], "team_id": task.get("team_id")}
    return data


@st.cache_data
def load_engineers(filepath: str) -> list[dict[str, Any]]:
    """Загружает профили инженеров для Звездной карты."""
    if not os.path.exists(filepath):
        st.warning(f"Файл инженеров не найден: {filepath}")
        return []
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def render_kpi_widgets(baseline_tasks: list[dict[str, Any]], current_tasks: list[dict[str, Any]]) -> None:
    """Отрисовывает виджеты KPI в шапке приложения.

    Args:
        baseline_tasks: Срез плана на Неделе 0.
        current_tasks: Текущий срез плана для расчета факта.
    """
    st.subheader("Квартальные KPI")
    st.caption("ℹ️ *Знаменатель обеих метрик зафиксирован на Неделе 0 в Baseline и строго неизменен.*")

    if not baseline_tasks:
        st.info("Baseline пуст. Ожидание данных для расчета KPI.")
        return

    metrics = kpi_breakdown(baseline_tasks, current_tasks)

    pi_fact = metrics["pi_predictability"]["fact"]["value"]
    pi_norm = metrics["norms"]["pi_predictability"]

    say_do_list = metrics["say_do"]
    # Берем самый актуальный непустой спринт (или последний)
    latest_sd = say_do_list[-1] if say_do_list else {"sprint": 1, "value": 0.0}
    sd_val = latest_sd["value"]
    sd_norm = metrics["norms"]["say_do"]

    col1, col2 = st.columns(2)

    with col1:
        pi_in_norm = pi_norm[0] <= pi_fact <= pi_norm[1]
        st.metric(
            label=f"PI Predictability (Норма {pi_norm[0]}-{pi_norm[1]}%)",
            value=f"{pi_fact:.1f}%",
            delta="В пределах нормы" if pi_in_norm else "Отклонение от нормы",
            delta_color="normal" if pi_in_norm else "inverse"
        )

    with col2:
        sd_in_norm = sd_norm[0] <= sd_val <= sd_norm[1]
        st.metric(
            label=f"Sprint {latest_sd['sprint']} Say/Do Ratio (Норма {sd_norm[0]}-{sd_norm[1]}%)",
            value=f"{sd_val:.1f}%",
            delta="В пределах нормы" if sd_in_norm else "Отклонение от нормы",
            delta_color="normal" if sd_in_norm else "inverse"
        )


def main() -> None:
    """Инициализирует Streamlit-страницу и sidebar-навигацию."""
    st.set_page_config(page_title=APP_TITLE, layout="wide")

    # Применяем кастомные стили Glassmorphism
    inject_custom_css()

    # Заглушка для логотипа проекта
    if os.path.exists(LOGO_PATH):
        st.sidebar.image(LOGO_PATH, use_container_width=True)
    else:
        st.sidebar.markdown(f"*{LOGO_PATH} не найден*")

    st.sidebar.title("🧭 Навигация")

    # Имитация структуры плана (Baseline vs Current)
    view_mode = st.sidebar.radio(
        "Режим просмотра",
        options=["Текущий план (Current)", "Снимок (Baseline Неделя 0)"],
        index=0
    )

    page = st.sidebar.selectbox(
        "Раздел",
        options=["План (PI-цикл)", "Риски и Алерты", "Звёздная карта"]
    )

    st.sidebar.markdown("---")
    st.sidebar.info("Команда: Кронверкские псы\n\nХакатон: ПочтаТех")

    # Загрузка данных
    raw_tasks = load_data(DATA_PATH)
    engineers = load_engineers(ENGINEERS_PATH)

    # Формируем объекты baseline и current.
    baseline_tasks = raw_tasks
    current_tasks = raw_tasks

    active_plan = baseline_tasks if "Baseline" in view_mode else current_tasks

    # 1. Глобальные KPI всегда рендерятся сверху
    render_kpi_widgets(baseline_tasks, current_tasks)
    st.divider()

    # 2. Роутинг страниц
    if page == "План (PI-цикл)":
        render_plan_view(active_plan)

    elif page == "Риски и Алерты":
        mock_alerts = [
            {
                "type": "red", "task_id": "DB-202", "prodf_id_at_risk": "PRODF-7124",
                "description": "Прогнозируемое завершение задачи выходит за 12 неделю.", "sprint_detected": 5
            },
            {
                "type": "yellow", "task_id": "MP-102", "prodf_id_at_risk": "PRODF-7125",
                "description": "Сдвиг задачи вызывает каскадный сдвиг зависимой задачи MP-103.", "sprint_detected": 3
            },
            {
                "type": "orange", "task_id": "SRV-4012", "prodf_id_at_risk": "PRODF-7125",
                "description": "Дефицит часов роли «Разработчик .net» на следующий спринт (нужно 130, доступно 80).",
                "sprint_detected": 1
            }
        ]
        render_risk_view(mock_alerts)

    elif page == "Звёздная карта":
        mock_competencies_data = {
            "engineers": engineers,
            "bus_factors": {
                "Java": 12, "Spring Boot": 8, "PostgreSQL": 10,
                "ARKit": 1, "Scala": 1, "TOGAF": 1
            },
            "linked_tasks": {
                "ARKit": [{"task_id": "MOB-401", "prodf_id": "PRODF-8001"}],
                "Scala": [{"task_id": "BD-101", "prodf_id": "PRODF-8002"}],
                "TOGAF": [{"task_id": "ARC-001", "prodf_id": "PRODF-8005"}]
            }
        }
        render_star_map_view(mock_competencies_data)


if __name__ == "__main__":
    main()
