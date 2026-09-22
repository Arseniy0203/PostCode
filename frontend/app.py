# Ответственный: Человек 4 (frontend + tests + docs)
"""
Точка входа Streamlit-приложения сервиса PI-планирования.

Навигация между страницами через sidebar, поддержка режимов Baseline/Current
и безопасная загрузка данных с пересчетом факта.
"""

from __future__ import annotations

import os
import sys
import copy
import tempfile
from typing import Any

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import streamlit as st

from backend.ingestion.parse_dataset import parse_excel, filter_orphan_dependencies
from backend.planning.scheduler import build_baseline_plan
from backend.risk.alerts import collect_alerts
from backend.starmap.bus_factor import starmap_summary
from backend.recalculation.recalc import apply_sprint_fact
from backend.kpi.metrics import kpi_breakdown

from views.plan_view import render_plan_view
from views.risk_view import render_risk_view
from views.star_map_view import render_star_map_view


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
        /* Скрытие дефолтного меню и футера, но оставляем header для кнопки сайдбара */
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        
        /* Делаем фон хедера прозрачным */
        header {background-color: transparent !important;}

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

        /* Стилизация зоны drag-and-drop file_uploader */
        [data-testid="stFileUploader"] > section {
            background: rgba(255, 255, 255, 0.6) !important;
            backdrop-filter: blur(10px) !important;
            border: 2px dashed rgba(0, 51, 102, 0.4) !important;
            border-radius: 16px !important;
            box-shadow: 0 4px 15px rgba(31, 38, 135, 0.05) !important;
            transition: all 0.3s ease;
        }
        [data-testid="stFileUploader"] > section:hover {
            background: rgba(255, 255, 255, 0.9) !important;
            border-color: rgba(0, 51, 102, 0.8) !important;
        }

        /* 1. Синий фон для всех кнопок и иконки-подсказки (?) */
        [data-testid="stFileUploader"] button,
        [data-testid="stTooltipIcon"] {
            background-color: #003366 !important;
            border: none !important;
        }

        /* 2. Белый цвет текста и иконок внутри синих кнопок */
        [data-testid="stFileUploader"] button *,
        [data-testid="stTooltipIcon"] * {
            color: #ffffff !important;
            fill: #ffffff !important;
        }
        
        [data-testid="stFileUploader"] button:hover {
            background-color: #001f3f !important;
        }

        /* 3. Обычный текст (имена файлов, размер, "Drag and drop") остается темным */
        [data-testid="stFileUploaderDropzoneInstructions"] span,
        [data-testid="stFileUploaderDropzoneInstructions"] small,
        [data-testid="stFileInfo"] p,
        [data-testid="stFileInfo"] small,
        [data-testid="stFileInfo"] span {
            color: #1e293b !important;
        }
        </style>
        """,
        unsafe_allow_html=True
    )


@st.cache_data(show_spinner="Сборка Baseline и проверка рисков...")
def process_baseline(file_bytes: bytes) -> dict[str, Any]:
    """Парсит исходный датасет и инициализирует Baseline-план."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        parsed = parse_excel(tmp_path)
        clean_deps, _ = filter_orphan_dependencies(parsed["task_dependencies"], parsed["tasks"])

        # Динамический сбор Capacity из Team_history
        capacity = {"team_sp": {}, "engineer_hh": {}}
        for th in parsed.get("team_history", []):
            t_id = th.get("team_id")
            sp = th.get("velocity_sp")
            if t_id and sp:
                # 20% резерв по ТЗ (Focus Factor 0.8)
                capacity["team_sp"][t_id] = float(sp) * 0.8

        plan = build_baseline_plan(
            tasks=parsed["tasks"],
            capacity=capacity,
            dependencies=clean_deps,
            engineers=parsed["engineers_profiles"],
            estimates=parsed["estimates"]
        )

        alerts = [a.to_dict() for a in collect_alerts(plan, clean_deps, parsed["engineers_profiles"])]
        starmap = starmap_summary(plan, parsed["engineers_profiles"], parsed["estimates"])

        return {
            "parsed": parsed,
            "clean_deps": clean_deps,
            "plan": plan,
            "alerts": alerts,
            "starmap": starmap
        }
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@st.cache_data(show_spinner="Применение факта и пересчет текущего плана...")
def process_fact(base_data: dict[str, Any], fact_file_bytes: bytes) -> dict[str, Any]:
    """Обрабатывает загруженный факт, обновляет Plan.current и пересчитывает риски."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
        tmp.write(fact_file_bytes)
        tmp_path = tmp.name

    try:
        fact_parsed = parse_excel(tmp_path)
        updated_plan = copy.deepcopy(base_data["plan"])

        updated_current = apply_sprint_fact(
            plan=updated_plan,
            fact_tasks=fact_parsed["tasks"],
            fact_spent=fact_parsed["spent_time_roles"]
        )

        if isinstance(updated_current, list):
            updated_plan["current"] = updated_current
        elif isinstance(updated_current, dict) and "current" in updated_current:
            updated_plan = updated_current

        # Гарантируем иммутабельность Baseline
        updated_plan["baseline"] = base_data["plan"]["baseline"]

        alerts = [a.to_dict() for a in
                  collect_alerts(updated_plan, base_data["clean_deps"], base_data["parsed"]["engineers_profiles"])]
        starmap = starmap_summary(updated_plan, base_data["parsed"]["engineers_profiles"],
                                  base_data["parsed"]["estimates"])

        return {
            "plan": updated_plan,
            "alerts": alerts,
            "starmap": starmap
        }
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def render_kpi_widgets(baseline_tasks: list[dict[str, Any]], current_tasks: list[dict[str, Any]]) -> None:
    """Отрисовывает виджеты KPI в шапке приложения."""
    st.subheader("Квартальные KPI")
    st.caption("ℹ️ *Знаменатель обеих метрик зафиксирован на Неделе 0 в Baseline и строго неизменен.*")

    if not baseline_tasks:
        st.info("Baseline пуст. Ожидание данных для расчета KPI.")
        return

    metrics = kpi_breakdown(baseline_tasks, current_tasks)

    pi_fact = metrics["pi_predictability"]["fact"]["value"]
    pi_norm = metrics["norms"]["pi_predictability"]

    say_do_list = metrics["say_do"]
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
    inject_custom_css()

    if os.path.exists(LOGO_PATH):
        st.sidebar.image(LOGO_PATH, width="stretch")

    st.sidebar.title("🧭 Навигация")

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
    st.sidebar.markdown("---")

    # Зона 1: Базовая загрузка Master-файла
    if "dataset_state" not in st.session_state:
        st.title("📥 Загрузка данных планирования")
        uploaded_file = st.file_uploader(
            "Загрузите Master-датасет (.xlsx)",
            type=["xlsx"],
            help="Файл инициализации плана. Используется для формирования Baseline."
        )

        if not uploaded_file:
            st.warning("Пожалуйста, загрузите исходный Excel-файл датасета для начала работы.")
            return  # Блокируем рендер остального UI, пока нет файла

        st.session_state.dataset_state = process_baseline(uploaded_file.getvalue())

    base_state = st.session_state.dataset_state

    # Зона 2: Загрузка Факта (Сайдбар)
    fact_file = st.sidebar.file_uploader(
        "Загрузить результаты спринта (Факт)",
        type=["xlsx"],
        help="Загрузите файл с фактическими данными (списания часов) для пересчета плана"
    )

    if fact_file:
        active_state = process_fact(base_state, fact_file.getvalue())
        st.sidebar.success("Факт успешно загружен и пересчитан!")
    else:
        active_state = base_state

    plan_dict = active_state["plan"]
    alerts = active_state["alerts"]
    starmap = active_state["starmap"]

    # Рендер глобальных KPI
    render_kpi_widgets(plan_dict.get("baseline", []), plan_dict.get("current", []))
    st.divider()

    # Роутинг интерфейсов
    if page == "План (PI-цикл)":
        render_plan_view(plan_dict, view_mode)
    elif page == "Риски и Алерты":
        render_risk_view(alerts)
    elif page == "Звёздная карта":
        render_star_map_view(starmap)


if __name__ == "__main__":
    main()
