# Ответственный: Человек 4 (frontend + tests + docs)
"""Карточка задачи — переиспользуемый UI компонент."""

from __future__ import annotations

from typing import Any

import streamlit as st


def render_task_card(task: dict[str, Any]) -> None:
    """
    Отрисовывает детальную карточку задачи.

    Отображает метаданные, трудозатраты, зависимости и Блок Объяснимости.

    Args:
        task: Словарь с данными задачи (схема PlannedTask).
    """
    decision = task.get("decision", "unknown")

    # Визуальное выделение карточки
    with st.container(border=True):
        st.subheader(f"Задача: {task.get('task_id', 'N/A')} — {task.get('summary', 'Без названия')}")

        # 1. Основные параметры
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("PRODF-инициатива", task.get("Номер инициативы", "N/A") or task.get("prodf_id", "N/A"))
        col2.metric("Команда", task.get("team_id", "N/A"))
        col3.metric("Rung (Уровень)", str(task.get("rung", "N/A")))
        col4.metric("Story Points", str(task.get("estimation_sp", 0)))

        st.markdown("---")

        # 2. БЛОК ОБЪЯСНИМОСТИ
        st.markdown("### 🧠 Обоснование решения планировщика")

        if decision == "included":
            msg_type = st.success
            icon = "✅"
        elif decision == "postponed":
            msg_type = st.warning
            icon = "⏳"
        elif decision == "cancelled":
            msg_type = st.error
            icon = "❌"
        else:
            msg_type = st.info
            icon = "ℹ️"

        reason_code = task.get("reason_code", "NO_REASON_CODE_PROVIDED")
        reason_detail = task.get("reason_detail", "Подробное описание причины отсутствует.")

        msg_type(f"**Вердикт:** {decision.upper()} {icon}\n\n**Код причины:** `{reason_code}`\n\n**Пояснение:** {reason_detail}")

        # Вывод математических фактов решения
        reason_facts = task.get("reason_facts", {})
        if reason_facts:
            with st.expander("📊 Математические факты решения (reason_facts)"):
                st.json(reason_facts)

        st.markdown("---")

        # 3. Трудозатраты и зависимости
        col_labor, col_deps = st.columns(2)

        with col_labor:
            st.markdown("#### 🕒 Трудозатраты (Человеко-часы)")
            est_hh = task.get("estimated_hh")

            if isinstance(est_hh, dict):
                # Если бэкенд отдал словарь (как по схеме)
                for role, hours in est_hh.items():
                    st.write(f"- **{role}:** {hours} ч.")
            elif isinstance(est_hh, (int, float)):
                # Fallback для сырого датасета, где это число
                st.write(f"- **Общие трудозатраты:** {est_hh} ч.")
            else:
                st.write("Оценки в часах отсутствуют.")

            start = task.get('planned_start')
            end = task.get('planned_end')
            if start and end:
                st.caption(f"Распределение (Allocation): Спринт {start} — Спринт {end}")

        with col_deps:
            st.markdown("#### 🔗 Зависимости")
            # Мок логики зависимостей (в реальном приложении подтягивается из графа)
            blocks = task.get("blocks", [])
            blocked_by = task.get("blocked_by", [])

            if not blocks and not blocked_by:
                st.write("Нет строгих топологических зависимостей.")

            if blocked_by:
                st.write("**Блокируется задачами:**")
                for b in blocked_by:
                    st.write(f"- `{b}`")

            if blocks:
                st.write("**Блокирует задачи:**")
                for b in blocks:
                    st.write(f"- `{b}`")
