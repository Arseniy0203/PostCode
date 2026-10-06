"""Страница плана по спринтам (Канбан-сетка)."""

from __future__ import annotations

from typing import Any

import streamlit as st

from backend.export.report import excel_bytes, pdf_bytes
from .components import detail_grid
from .labels import DECISION_LABELS, STATUS_LABELS, decision_label
from .task_card import render_task_card


# Риск, о котором стоит знать прямо в сетке спринтов. info не берём: их много
# и они не про срыв, им место на странице рисков.
RISK_LABELS = {"red": "критический риск", "yellow": "каскадный сдвиг",
               "orange": "нехватка людей"}
RISK_ORDER = ["red", "yellow", "orange"]


def task_risks(alerts: list[dict[str, Any]] | None) -> dict[str, list[dict[str, Any]]]:
    """Алерты по задачам: и по самой задаче, и по тем, кого она задевает."""
    by_task: dict[str, list[dict[str, Any]]] = {}
    for alert in alerts or []:
        if not isinstance(alert, dict) or alert.get("type") not in RISK_LABELS:
            continue
        for key in [alert.get("task_id"), *(alert.get("affected_task_ids") or [])]:
            if key:
                by_task.setdefault(key, []).append(alert)
    return by_task


def _risk_mark(risks: list[dict[str, Any]]) -> str:
    """Самый тяжёлый риск задачи — подпись в строке спринта."""
    for risk_type in RISK_ORDER:
        if any(r.get("type") == risk_type for r in risks):
            return RISK_LABELS[risk_type]
    return ""


@st.cache_data(show_spinner=False)
def _export_files(tasks: list[dict[str, Any]], current_sprint: int, alerts: list[dict[str, Any]],
                  starmap: dict[str, Any], team: str | None) -> tuple[bytes, bytes]:
    """Excel и PDF для текущего среза. Кэш — чтобы не собирать файлы на каждый клик."""
    plan = {"current": tasks, "current_sprint": current_sprint}
    return (excel_bytes(plan, alerts, starmap, team=team),
            pdf_bytes(plan, alerts, starmap, team=team))


def _render_export(tasks: list[dict[str, Any]], plan: dict[str, Any], team: str | None,
                   alerts: list[dict[str, Any]] | None, starmap: dict[str, Any] | None) -> None:
    """Кнопки выгрузки плана: результат работы планировщика в файле."""
    st.markdown("### Выгрузка плана")
    scope = f"команда {team}" if team else "все команды, по листу и странице на каждую"
    st.caption(f"Excel — колонки датасета «плановый результат», «будет включено в спринт» и "
               f"«исполнитель» заполнены планировщиком, плюс листы «Алерты» и «Нанять и обучить». "
               f"PDF — печатный отчёт. В выгрузке: {scope}.")
    try:
        xlsx, pdf = _export_files(tasks, int(plan.get("current_sprint") or 0),
                                  alerts or [], starmap or {}, team)
    except Exception as exc:  # noqa: BLE001 — страница не должна падать из-за выгрузки
        st.warning(f"Не удалось собрать файлы выгрузки: {exc}")
        return
    suffix = f"_{team}" if team else ""
    col_xlsx, col_pdf = st.columns(2)
    col_xlsx.download_button(
        "Скачать Excel", data=xlsx, file_name=f"Квартальный_план{suffix}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        width="stretch")
    col_pdf.download_button(
        "Скачать PDF", data=pdf, file_name=f"Квартальный_план{suffix}.pdf",
        mime="application/pdf", width="stretch")


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


def render_plan_view(plan: dict[str, Any], view_mode_label: str,
                     alerts: list[dict[str, Any]] | None = None,
                     starmap: dict[str, Any] | None = None,
                     on_manual_change: Any = None) -> None:
    """
    Отрисовывает план по спринтам в виде канбан-сетки.

    Включает фильтры в верхней части и распределение задач по вкладкам спринтов.

    Args:
        plan: Структура Plan (словарь с baseline и current).
        view_mode_label: Строка с названием текущего хронологического среза.
        alerts: алерты текущего среза — попадают в выгрузку отдельным листом.
        starmap: звёздная карта — из неё берётся план найма для выгрузки.
        on_manual_change: обработчик ручного решения по задаче (снять / вернуть).
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
    detail_grid([("В квартале", f"{len(included)} из {len(open_tasks)} задач"),
                 ("Выполнено", len(done)), ("Частичная цель", len(partial)),
                 ("Перенесено / отменено", f"{len(postponed)} / {len(cancelled)}")], compact=True)
    if partial:
        with st.expander("Почему у части задач понижена цель"):
            detail_grid([("Причина", "Для части ролей нет специалистов в компании."),
                         ("Решение", "Работа доступных специалистов идёт по плану. Подробности и рекомендации есть в карточке задачи.")])

    manual = st.session_state.get("manual_defer", {})
    if manual:
        st.info("Снято с квартала вручную: "
                + ", ".join(f"**{tid}** — {note}" for tid, note in sorted(manual.items()))
                + ". Эти задачи не планируются, их ёмкость досталась остальным.")
        if on_manual_change and st.button("Вернуть все в планирование", key="manual_return_all"):
            manual.clear()
            on_manual_change()

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

    _render_export(filtered_tasks, plan, None if sel_team == "Все" else sel_team,
                   alerts, starmap)

    st.markdown("---")

    # --- Поиск задачи ---
    # Дополняет раскрывающиеся блоки ниже: когда известен id или название,
    # искать задачу по вкладкам спринтов не нужно.
    st.markdown("### Поиск задачи")
    task_mapping = {
        f"{t.get('task_id')} · {t.get('summary') or 'Без названия'}": t
        for t in filtered_tasks
    }
    NOT_SELECTED = "— не выбрано —"
    picked = st.selectbox(
        "Введите номер или название задачи, чтобы открыть карточку:",
        options=[NOT_SELECTED, *task_mapping],
        key="plan_task_search",
    )
    if picked != NOT_SELECTED:
        chosen = task_mapping[picked]
        render_task_card(chosen, task_risks(alerts).get(chosen.get("task_id")) or [],
                         on_manual_change, scope="search")

    st.markdown("---")

    # --- Сетка спринтов ---
    st.markdown("### Распределение по спринтам")
    st.caption("Нажмите на задачу, чтобы раскрыть карточку с причиной решения. "
               "Если по задаче есть риск, он назван в строке и показан в карточке.")
    risks_by_task = task_risks(alerts)


    # Группировка задач
    sprint_groups: dict[int, list] = {i: [] for i in range(1, 9)}

    for t in filtered_tasks:
        # planned_start в датасете — старая дата; спринт квартала — planned_start_sprint
        start_sprint = t.get("planned_start_sprint")
        if t.get("decision") in ("postponed", "cancelled"):
            sprint_groups[7].append(t)
        elif isinstance(start_sprint, int) and 1 <= start_sprint <= 6:
            # Если задача растянута, показываем её в спринте старта
            sprint_groups[start_sprint].append(t)
        elif t.get("status") == "Done":
            # Закрыто до Недели 0: в план квартала не входит, но карточка нужна
            sprint_groups[8].append(t)
        else:
            sprint_groups[7].append(t)

    # Вкладки для 6 спринтов + 1 для перенесённых/отменённых, в названии — число задач
    tabs = st.tabs([f"Спринт {i} · {len(sprint_groups[i])}" for i in range(1, 7)]
                   + [f"Перенесённые / отменённые · {len(sprint_groups[7])}",
                      f"Закрыты до квартала · {len(sprint_groups[8])}"])

    # Отрисовка задач в соответствующих вкладках
    for i, tab in enumerate(tabs):
        sprint_idx = i + 1
        with tab:
            tasks_in_sprint = sprint_groups.get(sprint_idx, [])
            if not tasks_in_sprint:
                st.caption("Нет задач." if sprint_idx > 6 else "Нет задач в этом спринте.")
                continue
            if sprint_idx == 8:
                st.caption("Выполнены до Недели 0: в обязательства квартала не входят "
                           "и в знаменатель KPI не попадают.")

            # Задача раскрывается на месте: карточка появляется прямо в спринте,
            # искать её в отдельном списке не нужно.
            for t in tasks_in_sprint:
                color = get_status_color(t.get("status", ""), t.get("decision", ""))
                sp = t.get("estimation_sp") or 0
                sp = f"{sp:g}" if isinstance(sp, (int, float)) else sp
                decision = decision_label(t)
                if t.get("reason_code") == "PARTIAL_TARGET":
                    decision += " · частичная цель"
                status = STATUS_LABELS.get(t.get("status"), t.get("status", ""))
                summary = t.get("summary") or "Без названия"

                # Цвет статуса — левая граница самого блока задачи. Отдельная
                # полоса над expander съезжала при переносе длинных заголовков.
                key = f"task-{t.get('task_id')}-{sprint_idx}"
                st.markdown(
                    f"<style>.st-key-{key} [data-testid='stExpander'] details "
                    f"{{border-left: 3px solid {color} !important;}}</style>",
                    unsafe_allow_html=True,
                )
                risks = risks_by_task.get(t.get("task_id")) or []
                mark = _risk_mark(risks)
                label = (f"{t.get('task_id')} · {summary} — {t.get('team_id', 'без команды')} · "
                         f"{sp} SP · {status} · {decision}"
                         + (f" · {mark}" if mark else ""))
                with st.container(key=key):
                    with st.expander(label):
                        render_task_card(t, risks, on_manual_change,
                                         scope=f"sprint{sprint_idx}")
