"""Карточка задачи — переиспользуемый UI компонент."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from .labels import decision_label, hours, sprint_range
from .text import collapse_task_lists

# Числа решения (reason_facts) человеческими словами; прочие ключи служебные.
FACT_LABELS = {
    "need_hh": "Нужно часов",
    "available_hh": "Доступно часов",
    "deficit_hh": "Не хватает часов",
    "per_sprint_hh": "Часов роли за спринт",
    "need_sp": "Нужно SP",
    "capacity_sp": "Ёмкость команды за спринт, SP",
    "missing_roles": "Нет в компании",
    "missing_share": "Доля часов без исполнителей",
    "busy_roles": "Роли заняты другими задачами",
    "borrowed": "Привлечены из других команд",
    "stack": "Нужный стек",
    "blocking_task_id": "Ждёт задачу",
    "blocking_team": "Команда блокирующей задачи",
    "score": "Скоринг",
    "rank": "Место в очереди",
}


def _fact_value(key: str, value: Any) -> str:
    if key == "missing_share":
        return f"{float(value):.0%}"
    if key in ("need_hh", "available_hh", "deficit_hh", "per_sprint_hh"):
        return f"{hours(value)} ч"
    if key in ("missing_roles", "busy_roles"):
        return ", ".join(f"{role} — {hours(h)} ч" for role, h in value.items())
    if key == "borrowed":
        return ", ".join(f"{eng} ({team})" for eng, team in value.items())
    if key == "stack":
        return "; ".join(f"{role}: {', '.join(techs)}" for role, techs in value.items())
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _render_facts(task: dict[str, Any]) -> None:
    facts = task.get("reason_facts") or {}
    rows = [(key, facts[key]) for key in FACT_LABELS
            if facts.get(key) not in (None, "", {}, [])]
    if not rows:
        return
    if "rank" in facts and facts.get("total"):
        facts = {**facts, "rank": f"{facts['rank']} из {facts['total']}"}
    with st.expander("Расчёт, на котором основано решение"):
        for key, _ in rows:
            st.markdown(f"- **{FACT_LABELS[key]}:** {_fact_value(key, facts[key])}")


RISK_TITLES = {"red": "Критический риск", "yellow": "Каскадный сдвиг",
               "orange": "Нехватка людей"}


def _render_risks(risks: list[dict[str, Any]]) -> None:
    """Риски этой задачи — чтобы не искать её на странице алертов."""
    if not risks:
        return
    st.markdown("**Риски по задаче**")
    for risk in risks:
        title = RISK_TITLES.get(risk.get("type"), "Риск")
        description, _ = collapse_task_lists(risk.get("description", ""))
        st.markdown(f"- **{title}.** {description}")


def _render_manual_controls(task: dict[str, Any], on_change: Any) -> None:
    """Ручное решение по задаче: снять с квартала или вернуть.

    Планировщик предлагает, решает человек: PI-планирование — это переговоры.
    После решения план пересобирается, освободившаяся ёмкость уходит другим.
    """
    if on_change is None or task.get("status") == "Done":
        return
    tid = task.get("task_id")
    manual = st.session_state.get("manual_defer", {})
    if tid in manual:
        st.caption("Задача снята с квартала вручную.")
        if st.button("Вернуть в планирование", key=f"manual_return_{tid}", width="stretch"):
            manual.pop(tid, None)
            on_change()
    else:
        with st.popover("Снять с квартала", width="stretch"):
            note = st.text_input("Причина (попадёт в план и выгрузку)",
                                 key=f"manual_note_{tid}",
                                 placeholder="например: заказчик отложил до следующего квартала")
            if st.button("Подтвердить", key=f"manual_defer_{tid}", width="stretch"):
                manual[tid] = note.strip() or "решение руководителя"
                on_change()


def render_task_card(task: dict[str, Any], risks: list[dict[str, Any]] | None = None,
                     on_manual_change: Any = None) -> None:
    """
    Отрисовывает детальную карточку задачи.

    Отображает метаданные, решение с причиной и целью, рекомендацию, трудозатраты
    и зависимости.

    Args:
        task: Словарь с данными задачи (схема PlannedTask).
        risks: алерты, которые касаются этой задачи (см. plan_view.task_risks).
        on_manual_change: что вызвать после ручного решения по задаче; None —
            кнопки не показываются (например, в снимке Недели 0).
    """
    decision = task.get("decision")

    # Визуальное выделение карточки
    with st.container(border=True):
        st.subheader(f"{task.get('task_id', 'N/A')} — {task.get('summary', 'Без названия')}")

        # 1. Основные параметры — одной строкой (крупные метрики обрезали длинные значения)
        sp = task.get("estimation_sp")
        st.markdown(" · ".join([
            f"Инициатива **{task.get('Номер инициативы') or task.get('prodf_id') or '—'}**",
            f"команда **{task.get('team_id') or '—'}**",
            f"приоритет (rung) **{task.get('rung', '—')}**",
            f"**{sp:g} SP**" if isinstance(sp, (int, float)) else "SP —",
        ]))

        # 2. Решение и его причина
        msg_type = {"included": st.success, "postponed": st.warning,
                    "cancelled": st.error}.get(decision, st.info)
        lines = [f"**{decision_label(task)}**"
                 + (f" · {task['reason_label']}" if task.get("reason_label") else "")]
        if task.get("target_label"):
            lines.append(f"**Цель в квартале:** {task['target_label']}")
        if task.get("reason_detail"):
            lines.append(task["reason_detail"])
        if task.get("recommendation"):
            lines.append(f"**Что можно сделать:** {task['recommendation']}")
        msg_type("\n\n".join(lines))

        _render_facts(task)
        _render_risks(risks or [])
        _render_manual_controls(task, on_manual_change)

        # 3. Трудозатраты и зависимости
        col_labor, col_deps = st.columns(2)

        with col_labor:
            st.markdown("**Трудозатраты по ролям**")
            est_hh = task.get("estimated_hh")
            unstaffed = task.get("unstaffed_hh") or {}

            if isinstance(est_hh, dict) and est_hh:
                for role, value in est_hh.items():
                    note = " — нет исполнителя" if role in unstaffed else ""
                    st.write(f"- {role}: {hours(value)} ч{note}")
            elif isinstance(est_hh, (int, float)):
                # Fallback для сырого датасета, где это число
                st.write(f"- Всего: {hours(est_hh)} ч")
            else:
                st.write("Оценки в часах отсутствуют.")

            when = sprint_range(task.get("planned_start_sprint"), task.get("planned_end_sprint"))
            if when:
                st.caption(f"Сроки: {when}")

            allocation = task.get("allocation")
            if allocation and isinstance(allocation, dict):
                with st.expander("Кто и когда работает"):
                    # Таблица вместо серых подписей: читается на экране и на проекторе
                    rows = []
                    for sprint, engineers in allocation.items():
                        if isinstance(engineers, dict):
                            people = ", ".join(f"{eng} — {hours(value)} ч"
                                               for eng, value in engineers.items())
                            total = sum(float(v or 0) for v in engineers.values())
                        else:
                            people, total = str(engineers), 0.0
                        rows.append({"Спринт": sprint, "Кто работает": people,
                                     "Часов": hours(total)})
                    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

        with col_deps:
            st.markdown("**Зависимости**")
            blocks = task.get("blocks") or []
            blocked_by = task.get("blocked_by") or []

            if not blocks and not blocked_by:
                st.write("Нет зависимостей.")
            if blocked_by:
                st.write("Ждёт задачи: " + ", ".join(f"`{b}`" for b in blocked_by))
            if blocks:
                st.write("Блокирует задачи: " + ", ".join(f"`{b}`" for b in blocks))
