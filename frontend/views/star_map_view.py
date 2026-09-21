# Ответственный: Человек 4 (frontend + tests + docs)
"""Страница звёздной карты компетенций."""

from __future__ import annotations

from typing import Any
import streamlit as st
import pandas as pd


def render_star_map_view(competencies: Any) -> None:
    """Отрисовывает таблицу компетенций.

    Подсвечивает компетенции с Bus Factor = 1 (см.
    backend/starmap/bus_factor.py::find_single_points_of_failure) и связывает
    их с задачами текущего плана (см. bus_factor.py::link_risk_to_tasks).

    Args:
        competencies: данные звёздной карты (профили команд/инженеров,
            Bus Factor по компетенциям, связка с задачами).
    """
    st.header("Звёздная карта компетенций")

    engineers = competencies.get("engineers", [])
    bus_factors = competencies.get("bus_factors", {})
    linked_tasks = competencies.get("linked_tasks", {})

    if not engineers:
        st.warning("Нет данных о профилях инженеров для построения карты.")
        return

    # Подготовка данных для матрицы
    matrix_data = []
    for skill, bf in bus_factors.items():
        owners = [e for e in engineers if skill in e.get("skills_declared", [])]
        owners_labels = [f"{e.get('engineer_id')} ({e.get('team_id')}, {e.get('capacity_rate')} rate)" for e in owners]

        matrix_data.append({
            "Компетенция / Технология": skill,
            "Bus Factor": bf,
            "Носители компетенции": ", ".join(owners_labels)
        })

    df = pd.DataFrame(matrix_data)

    def highlight_critical_bf(val: int) -> str:
        """Подсвечивает ячейку красным, если Bus Factor равен 1."""
        return 'background-color: #ff4b4b; color: white; font-weight: bold;' if val == 1 else ''

    st.subheader("Матрица компетенций")
    st.dataframe(
        df.style.map(highlight_critical_bf, subset=['Bus Factor']),
        width='stretch',
        hide_index=True
    )

    st.subheader("🔥 Зона критического риска (Bus Factor = 1)")
    critical_skills = [skill for skill, bf in bus_factors.items() if bf == 1]

    if not critical_skills:
        st.success("Уникальных компетенций-одиночек не обнаружено. Bus Factor в норме!")
    else:
        for skill in critical_skills:
            tasks_at_risk = linked_tasks.get(skill, [])
            st.error(f"**{skill}** — уникальная компетенция!")

            if tasks_at_risk:
                st.markdown("**Зависимые задачи в текущем плане, находящиеся под риском:**")
                for task in tasks_at_risk:
                    task_id = task.get("task_id", "Unknown")
                    prodf = task.get("prodf_id", "Unknown PRODF")
                    st.markdown(f"- ⚠️ Задача: `{task_id}` (Цель: `{prodf}`)")
            else:
                st.markdown("- *Зависимых задач в текущем плане не обнаружено.*")
