# Ответственный: Человек 4 (frontend + tests + docs)
"""Страница плана по спринтам (Канбан-сетка)."""

from __future__ import annotations

from typing import Any

import streamlit as st

from .labels import DECISION_LABELS, STATUS_LABELS, decision_label
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
        return "#B3261E"  # Отменено
    if decision == "postponed":
        return "#9A6100"  # Перенесено

    status_map = {
        "Done": "#1B6D45",        # Выполнено
        "InProgress": "#294DA6",  # В работе
        "ToDo": "#7A8494"         # В очереди
    }
    return status_map.get(status, "#9AA3B0")


def render_plan_view(plan: dict[str, Any], view_mode_label: str) -> None:
    """
    Отрисовывает план по спринтам в виде канбан-сетки.

    Включает фильтры в верхней части и распределение задач по вкладкам спринтов.

    Args:
        plan: Структура Plan (словарь с baseline и current).
        view_mode_label: Строка с названием текущего хронологического среза.
    """
    st.title(f"Квартальный план · {view_mode_label}")

    # Читаем baseline только если выбран снимок Недели 0
    tasks = plan.get("baseline", []) if "Baseline" in view_mode_label else plan.get("current", [])

    if not tasks:
        st.warning("Нет данных для отображения.")
        return

    # --- Сводка: что решил планировщик ---
    open_tasks = [t for t in tasks if t.get("decision")]
    included = [t for t in open_tasks if t.get("decision") == "included"]
    partial = [t for t in included if t.get("reason_code") == "PARTIAL_TARGET"]
    postponed = [t for t in open_tasks if t.get("decision") == "postponed"]
    cancelled = [t for t in open_tasks if t.get("decision") == "cancelled"]
    done = [t for t in included if t.get("status") == "Done"]
    summary = (f"В квартале **{len(included)}** из {len(open_tasks)} задач"
               + (f" (выполнено {len(done)})" if done else "")
               + (f", из них с частичной целью — {len(partial)}" if partial else "")
               + f". Перенесено: **{len(postponed)}**"
               + (f", отменено: **{len(cancelled)}**" if cancelled else "") + ".")
    st.markdown(summary)
    if partial:
        st.caption("Частичная цель — для части ролей (руководитель проекта, поддержка, 1С) нет "
                   "людей в компании: остальная работа идёт, цель понижена по справочнику "
                   "«Варианты выбора цели». Подробности — в карточке задачи.")

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
        sel_status = st.selectbox("Статус", options=["Все", *STATUS_LABELS],
                                  format_func=lambda v: STATUS_LABELS.get(v, v))
    with col4:
        sel_decision = st.selectbox("Решение", options=["Все", *DECISION_LABELS],
                                    format_func=lambda v: DECISION_LABELS.get(v, v))

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


    # Группировка задач
    sprint_groups: dict[int, list] = {i: [] for i in range(1, 8)}

    for t in filtered_tasks:
        # planned_start в датасете — старая дата; спринт квартала — planned_start_sprint
        start_sprint = t.get("planned_start_sprint")
        if t.get("decision") in ("postponed", "cancelled"):
            sprint_groups[7].append(t)
        elif isinstance(start_sprint, int) and 1 <= start_sprint <= 6:
            # Если задача растянута, показываем её в спринте старта
            sprint_groups[start_sprint].append(t)
        elif t.get("status") != "Done":
            # Done до квартала — история, в сетку спринтов не попадает
            sprint_groups[7].append(t)

    # Вкладки для 6 спринтов + 1 для перенесённых/отменённых, в названии — число задач
    tabs = st.tabs([f"Спринт {i} · {len(sprint_groups[i])}" for i in range(1, 7)]
                   + [f"Перенесённые / отменённые · {len(sprint_groups[7])}"])

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
                sp = t.get("estimation_sp") or 0
                sp = f"{sp:g}" if isinstance(sp, (int, float)) else sp
                decision = decision_label(t)
                if t.get("reason_code") == "PARTIAL_TARGET":
                    decision += " · частичная цель"
                status = STATUS_LABELS.get(t.get("status"), t.get("status", ""))

                st.markdown(
                    f"""
                    <div style="border-left: 3px solid {color}; padding: 10px 14px; margin-bottom: 8px; background: #F7F8FA; border-radius: 4px;">
                        <strong>{t.get('task_id')}</strong> | {t.get('team_id', 'Unassigned')} | SP: {sp} | {status} | {decision}<br>
                        <small>{t.get('summary', '')}</small>
                    </div>
                    """,
                    unsafe_allow_html=True
                )
