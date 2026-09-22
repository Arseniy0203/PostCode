# Ответственный: Человек 4 (frontend + tests + docs)
"""Карточка задачи — переиспользуемый UI компонент."""

from __future__ import annotations

from typing import Any

import streamlit as st

from .labels import decision_label, hours, sprint_range

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


def render_task_card(task: dict[str, Any]) -> None:
    """
    Отрисовывает детальную карточку задачи.

    Отображает метаданные, решение с причиной и целью, рекомендацию, трудозатраты
    и зависимости.

    Args:
        task: Словарь с данными задачи (схема PlannedTask).
    """
    decision = task.get("decision")

    # Визуальное выделение карточки
    with st.container(border=True):
        st.subheader(f"{task.get('task_id', 'N/A')} — {task.get('summary', 'Без названия')}")

        # 1. Основные параметры
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Инициатива", task.get("Номер инициативы") or task.get("prodf_id") or "—")
        col2.metric("Команда", task.get("team_id") or "—")
        col3.metric("Приоритет (rung)", str(task.get("rung", "—")))
        col4.metric("Story Points", f"{task.get('estimation_sp', 0):g}"
                    if isinstance(task.get("estimation_sp"), (int, float)) else "—")

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
                    for s, engs in allocation.items():
                        if isinstance(engs, dict):
                            eng_str = ", ".join(f"{e} ({hours(h)} ч)" for e, h in engs.items())
                            st.caption(f"Спринт {s}: {eng_str}")
                        else:
                            st.caption(f"Спринт {s}: {engs}")

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
