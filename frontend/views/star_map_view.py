# Ответственный: Человек 4 (frontend + tests + docs)
"""Страница звёздной карты компетенций."""

from __future__ import annotations

from typing import Any
import streamlit as st
from streamlit_agraph import agraph, Node, Edge, Config


def render_star_map_view(competencies: Any) -> None:
    """Отрисовывает интерактивный граф звёздной карты компетенций.

    Подсвечивает компетенции с Bus Factor = 1 красным цветом.
    При клике на узел-компетенцию динамически отображает зависимые задачи
    текущего плана, находящиеся под риском.

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

    # Настройки визуализации
    NODE_SIZE_TECH = 30
    NODE_SIZE_ENG = 15
    COLOR_CRITICAL = "#FF4B4B"  # Ярко-красный (Bus Factor = 1)
    COLOR_NORMAL = "#1F77B4"  # Фирменный синий
    COLOR_ENG = "#A9A9A9"  # Нейтральный серый

    nodes = []
    edges = []
    added_engineers = set()
    added_skills = set()

    # Определяем зону риска (Bus Factor = 1)
    critical_skills = {skill for skill, bf in bus_factors.items() if bf == 1}

    # Построение узлов и рёбер графа
    for eng in engineers:
        eng_id = eng.get("engineer_id")
        if eng_id and eng_id not in added_engineers:
            nodes.append(Node(
                id=eng_id,
                label=eng_id,
                size=NODE_SIZE_ENG,
                color=COLOR_ENG,
                title=f"Инженер: {eng_id} ({eng.get('role', 'N/A')})"
            ))
            added_engineers.add(eng_id)

        for skill in eng.get("skills_declared", []):
            if skill not in added_skills:
                is_critical = skill in critical_skills
                nodes.append(Node(
                    id=skill,
                    label=skill,
                    size=NODE_SIZE_TECH,
                    color=COLOR_CRITICAL if is_critical else COLOR_NORMAL,
                    title=f"Компетенция: {skill} (Bus Factor: {bus_factors.get(skill, 0)})"
                ))
                added_skills.add(skill)

            # Создаем ребро, связывающее инженера с технологией
            edges.append(Edge(source=eng_id, target=skill))

    # Конфигурация интерактивности графа
    config = Config(
        width="100%",
        height=600,
        directed=False,
        physics=True,
        hierarchical=False,
        nodeHighlightBehavior=True
    )

    st.subheader("Интерактивный граф компетенций")
    st.markdown(
        "Перетаскивайте узлы и используйте колесо мыши для масштабирования. "
        "**Кликните на красный узел**, чтобы увидеть список бизнес-задач под риском.")

    # Отрисовка графа с захватом ID кликнутого узла
    clicked_node_id = agraph(nodes=nodes, edges=edges, config=config)

    st.subheader("🔥 Зона критического риска (Bus Factor = 1)")

    if not critical_skills:
        st.success("Уникальных компетенций-одиночек не обнаружено. Bus Factor в норме!")
    else:
        # Динамическая обработка пользовательского взаимодействия
        if clicked_node_id in critical_skills:
            tasks_at_risk = linked_tasks.get(clicked_node_id, [])
            st.error(f"**{clicked_node_id}** — уникальная компетенция!")

            if tasks_at_risk:
                st.markdown("**Зависимые задачи в текущем плане, находящиеся под угрозой:**")
                for task in tasks_at_risk:
                    task_id = task.get("task_id", "Unknown")
                    prodf = task.get("prodf_id", "Unknown PRODF")
                    st.markdown(f"- ⚠️ Задача: `{task_id}` (Инициатива: `{prodf}`)")
            else:
                st.markdown("- *Зависимых задач в текущем плане не обнаружено.*")

        elif clicked_node_id in added_skills:
            st.info(
                f"Компетенция **{clicked_node_id}** имеет безопасный Bus Factor: "
                f"{bus_factors.get(clicked_node_id, 0)}.")

        elif clicked_node_id in added_engineers:
            st.info(f"Выбран инженер: **{clicked_node_id}**.")

        else:
            st.info("👆 Кликните на красный узел компетенции на графе, чтобы развернуть детали рисков.")
