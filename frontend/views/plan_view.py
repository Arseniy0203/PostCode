# Ответственный: Человек 4 (frontend + tests + docs)
"""Страница плана по спринтам (Канбан-сетка)."""

from __future__ import annotations

from typing import Any

import streamlit as st

from .task_card import render_task_card


def get_status_color(status: str, decision: str) -> str:
    """
    Возвращает CSS-цвет для статуса или решения задачи.

    Args:
        status: Статус задачи (Done, InProgress, ToDo).
        decision: Решение (included, postponed, cancelled).

    Returns:
        Hex-код цвета.
    """
    if decision == "cancelled":
        return "#d32f2f"  # Красный
    if decision == "postponed":
        return "#f57c00"  # Оранжевый

    status_map = {
        "Done": "#388e3c",        # Зеленый
        "InProgress": "#1976d2",  # Синий
        "ToDo": "#78909c"         # Серо-голубой
    }
    return status_map.get(status, "#9e9e9e")


def render_plan_view(tasks: list[dict[str, Any]]) -> None:
    """
    Отрисовывает план по спринтам в виде канбан-сетки.

    Включает фильтры в верхней части и распределение задач по вкладкам спринтов.

    Args:
        tasks: Список задач из current или baseline плана.
    """
    st.title("📅 Квартальный PI-план (6 спринтов)")

    if not tasks:
        st.warning("Нет данных для отображения.")
        return

    # --- Блок фильтров ---
    st.markdown("### Фильтры")
    col1, col2, col3, col4 = st.columns(4)

    teams = sorted(list({t.get("team_id") for t in tasks if t.get("team_id")}))
    prodfs = sorted(list({t.get("Номер инициативы") for t in tasks if t.get("Номер инициативы")}))

    with col1:
        sel_team = st.selectbox("Команда", options=["Все"] + teams)
    with col2:
        sel_prodf = st.selectbox("Инициатива (PRODF)", options=["Все"] + prodfs)
    with col3:
        sel_status = st.selectbox("Статус", options=["Все", "Done", "InProgress", "ToDo"])
    with col4:
        sel_decision = st.selectbox("Решение", options=["Все", "included", "postponed", "cancelled"])

    # Применение фильтров
    filtered_tasks = tasks
    if sel_team != "Все":
        filtered_tasks = [t for t in filtered_tasks if t.get("team_id") == sel_team]
    if sel_prodf != "Все":
        filtered_tasks = [t for t in filtered_tasks if t.get("Номер инициативы") == sel_prodf]
    if sel_status != "Все":
        filtered_tasks = [t for t in filtered_tasks if t.get("status") == sel_status]
    if sel_decision != "Все":
        filtered_tasks = [t for t in filtered_tasks if t.get("decision") == sel_decision]

    st.markdown("---")

    # --- Выбор задачи для детального просмотра ---
    task_mapping = {f"{t['task_id']} ({t.get('summary', 'Без названия')})": t for t in filtered_tasks}

    st.markdown("### Детальная информация по задаче")
    selected_task_label = st.selectbox(
        "Выберите задачу для просмотра карточки и объяснения решения:",
        options=["-- Не выбрано --"] + list(task_mapping.keys())
    )

    if selected_task_label != "-- Не выбрано --":
        render_task_card(task_mapping[selected_task_label])

    st.markdown("---")

    # --- Сетка спринтов ---
    st.markdown("### Распределение по спринтам")

    # Создаем вкладки для 6 спринтов + 1 для перенесенных/отмененных
    tabs = st.tabs([f"Спринт {i}" for i in range(1, 7)] + ["Отложенные / Отмененные"])

    # Группировка задач
    sprint_groups: dict[int, list] = {i: [] for i in range(1, 8)}

    for t in filtered_tasks:
        if t.get("decision") in ("postponed", "cancelled") or not t.get("planned_start"):
            sprint_groups[7].append(t)
        else:
            # Если задача растянута, показываем её в спринте старта
            start_sprint = int(t.get("planned_start", 7))
            if 1 <= start_sprint <= 6:
                sprint_groups[start_sprint].append(t)
            else:
                sprint_groups[7].append(t)

    # Отрисовка задач в соответствующих вкладках
    for i, tab in enumerate(tabs):
        sprint_idx = i + 1
        with tab:
            tasks_in_sprint = sprint_groups.get(sprint_idx, [])
            if not tasks_in_sprint:
                st.info("Нет задач в этом спринте.")
                continue

            # Выводим задачи компактными карточками
            for t in tasks_in_sprint:
                color = get_status_color(t.get("status", ""), t.get("decision", ""))
                sp = t.get("estimation_sp", 0)
                decision = t.get("decision", "unknown").upper()

                st.markdown(
                    f"""
                    <div style="border-left: 5px solid {color}; padding: 10px; margin-bottom: 10px; background-color: rgba(128,128,128,0.1); border-radius: 4px;">
                        <strong>{t.get('task_id')}</strong> | {t.get('team_id', 'Unassigned')} | SP: {sp} | {decision}<br>
                        <small>{t.get('summary', '')}</small>
                    </div>
                    """,
                    unsafe_allow_html=True
                )
