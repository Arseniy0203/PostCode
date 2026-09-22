# Ответственный: Человек 4 (frontend + tests + docs)
"""Страница звёздной карты компетенций."""

from __future__ import annotations

from typing import Any
import pandas as pd
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
    missing_competencies = competencies.get("missing_competencies", [])
    missing_tasks = competencies.get("missing_competency_tasks", {})

    if not engineers:
        st.warning("Нет данных о профилях инженеров для построения карты.")
        return

    # Настройки визуализации: компетенции — звёзды, инженеры — точки.
    # Размер звезды растёт с числом носителей, поэтому одиночки визуально мельче
    # и ярче выделяются красным.
    NODE_SIZE_TECH = 26
    NODE_SIZE_ENG = 9
    COLOR_CRITICAL = "#B3261E"  # Приглушённый красный: Bus Factor = 1
    COLOR_NORMAL = "#294DA6"  # Синий из логотипа «почтатех»
    COLOR_ENG = "#9AA3B0"  # Нейтральный серый: инженеры
    COLOR_MISSING = "#B3261E"  # Контур красный, заливки нет: носителей нет вовсе

    nodes = []
    edges = []
    added_engineers = set()
    added_skills = set()

    # Определяем зону риска (Bus Factor = 1)
    critical_skills = {skill for skill, bf in bus_factors.items() if bf == 1}

    # Построение узлов и рёбер графа
    for eng in engineers:
        eng_id = eng.get("engineer_id")

        # Оставляем только те компетенции, которые нужны плану и посчитаны бэкендом
        relevant_skills = [skill for skill in eng.get("skills_declared", []) if skill in bus_factors]

        # Если у инженера нет нужных плану компетенций, не рисуем его висящим в воздухе
        if eng_id and eng_id not in added_engineers and relevant_skills:
            nodes.append(Node(
                id=eng_id,
                label=eng_id,
                shape="dot",
                size=NODE_SIZE_ENG,
                color=COLOR_ENG,
                font={"size": 11, "color": "#5B6472", "face": "Inter"},
                title=f"{eng_id} · {eng.get('role', 'роль не указана')}"
            ))
            added_engineers.add(eng_id)

        for skill in relevant_skills:
            if skill not in added_skills:
                is_critical = skill in critical_skills
                bf = bus_factors.get(skill, 0)
                nodes.append(Node(
                    id=skill,
                    label=skill,
                    shape="star",
                    size=NODE_SIZE_TECH + min(bf, 6) * 3,
                    color=COLOR_CRITICAL if is_critical else COLOR_NORMAL,
                    font={"size": 13, "color": "#111827", "face": "Inter",
                          "bold": {"color": "#111827"}},
                    borderWidth=0,
                    title=f"{skill} · Bus Factor {bf}"
                ))
                added_skills.add(skill)

            # Создаем ребро, связывающее инженера с технологией
            edges.append(Edge(source=eng_id, target=skill, color="#D7DBE3", width=1))

    # Конфигурация интерактивности графа
    config = Config(
        width="100%",
        height=620,
        directed=False,
        physics=True,
        hierarchical=False,
        nodeHighlightBehavior=True,
        highlightColor="#294DA6",
        backgroundColor="#FFFFFF",
    )

    st.subheader("Интерактивный граф компетенций")
    st.caption(
        "Звёзды — компетенции, точки — инженеры. Размер звезды растёт с числом "
        "носителей: сплошные красные держатся на одном человеке, пустые с красным "
        "контуром не имеют носителей вовсе. Выберите звезду, чтобы увидеть задачи "
        "под риском.")

    show_missing = st.checkbox(
        f"Показать компетенции без носителей · Bus Factor 0 ({len(missing_competencies)})",
        value=bool(missing_competencies),
        help="План требует эти компетенции, но в компании нет ни одного инженера. "
             "Связей у таких звёзд нет — именно в этом и состоит риск.",
        disabled=not missing_competencies,
    )

    if show_missing:
        for skill in missing_competencies:
            nodes.append(Node(
                id=skill,
                label=skill,
                shape="star",
                size=NODE_SIZE_TECH,
                color={"background": "#FFFFFF", "border": COLOR_MISSING,
                       "highlight": {"background": "#FDECEA", "border": COLOR_MISSING}},
                borderWidth=2,
                borderWidthSelected=3,
                font={"size": 13, "color": COLOR_MISSING, "face": "Inter"},
                title=f"{skill} · Bus Factor 0 — носителей нет",
            ))

    # Отрисовка графа с захватом ID кликнутого узла
    clicked_node_id = agraph(nodes=nodes, edges=edges, config=config)

    st.subheader("Зона риска")

    if not critical_skills and not missing_competencies:
        st.success("Компетенций с единственным носителем не обнаружено.")
    else:
        st.caption(
            f"Компетенций с единственным носителем: {len(critical_skills)}. "
            f"Без носителей вовсе: {len(missing_competencies)}.")

        # Динамическая обработка пользовательского взаимодействия
        if clicked_node_id in critical_skills:
            tasks_at_risk = linked_tasks.get(clicked_node_id, [])
            st.error(f"**{clicked_node_id}** — уникальная компетенция!")

            if tasks_at_risk:
                st.markdown("**Зависимые задачи в текущем плане, находящиеся под угрозой:**")
                for task in tasks_at_risk:
                    task_id = task.get("task_id", "Unknown")
                    prodf = task.get("prodf_id", "Unknown PRODF")
                    role = task.get("role", "N/A")
                    st.markdown(f"- `{task_id}` · инициатива `{prodf}` · роль {role}")
            else:
                st.markdown("- *Зависимых задач в текущем плане не обнаружено.*")

        elif clicked_node_id in missing_competencies:
            st.error(f"**{clicked_node_id}** — Bus Factor 0: носителей нет в компании.")
            tasks = missing_tasks.get(clicked_node_id, [])
            if tasks:
                st.markdown("**Задачи плана, которым нужна эта компетенция:**")
                for tid in tasks:
                    st.markdown(f"- `{tid}`")
            else:
                st.markdown("- *Задач в текущем плане не обнаружено.*")

        elif clicked_node_id in added_skills:
            st.info(
                f"Компетенция **{clicked_node_id}** имеет безопасный Bus Factor: "
                f"{bus_factors.get(clicked_node_id, 0)}.")

        elif clicked_node_id in added_engineers:
            profile = next((e for e in engineers
                            if e.get("engineer_id") == clicked_node_id), {})
            teams = ", ".join(profile.get("teams", {})) or "команда не указана"
            st.info(f"**{clicked_node_id}** · {profile.get('role', 'роль не указана')} · {teams}")

        else:
            st.caption("Выберите красную звезду на графе, чтобы увидеть задачи под риском.")

    _render_recommendations(competencies.get("recommendations") or {})
    _render_team_bus_factor(competencies.get("team_bus_factor") or {})


STATUS_ORDER = {"missing": 0, "single": 1, "borrowed": 2, "ok": 3}


def _tasks_word(n: int) -> str:
    return "задача" if n % 10 == 1 and n % 100 != 11 else (
        "задачи" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "задач")


def _render_recommendations(recs: dict[str, Any]) -> None:
    """Кого нанять и кого обучить — ответ экспертов: главное после Bus Factor."""
    hire, train = recs.get("hire") or [], recs.get("train") or []
    if not hire and not train:
        return
    st.subheader("Что делать")
    col_hire, col_train = st.columns(2)
    with col_hire:
        st.markdown("**Нанять** — ролей нет ни у одного инженера")
        for item in hire:
            n = len(item.get("tasks") or [])
            line = f"- **{item['role']}** — {item['hours']:g} ч, {n} {_tasks_word(n)}"
            alt = (item.get("train_instead") or [None])[0]
            if alt:
                line += f"; быстрее — обучить {alt['engineer_id']} ({alt['why']})"
            st.markdown(line)
        if not hire:
            st.caption("Все роли плана есть в компании.")
    with col_train:
        st.markdown("**Обучить** — роль держится на одном человеке")
        for item in train:
            cand = item.get("candidate") or {}
            n = len(item.get("tasks") or [])
            st.markdown(
                f"- **{cand.get('engineer_id', '—')}** ({cand.get('role', '')}) → «{item['role']}»: "
                f"{cand.get('why', '')}. Сейчас {n} {_tasks_word(n)} только на {item['owner_id']}")
        if not train:
            st.caption("Ролей с единственным носителем нет.")


def _render_team_bus_factor(teams: dict[str, Any]) -> None:
    """Bus Factor внутри команды (ответ экспертов: считать по команде)."""
    if not teams:
        return
    st.subheader("Bus Factor внутри команд")
    col_team, col_filter = st.columns([2, 1])
    team = col_team.selectbox("Команда", options=sorted(teams), key="bf_team")
    only_risks = col_filter.toggle("Только риски", value=True, key="bf_only_risks")
    data = teams[team]
    rows = sorted(data.get("rows") or [],
                  key=lambda r: (STATUS_ORDER.get(r.get("status"), 9), -(r.get("hours") or 0)))
    if only_risks:
        rows = [r for r in rows if r.get("status") != "ok"]
    if not rows:
        st.success("В команде нет компетенций с Bus Factor ≤ 1.")
        return
    table = pd.DataFrame([{
        "Компетенция": r["competency"] + ("" if r.get("level") == "role" else f" ({r.get('role', '')})"),
        "Bus Factor": r.get("bus_factor", 0),
        "Носители в команде": ", ".join(r.get("in_team") or []) or "—",
        "Состояние": r.get("status_label", ""),
        "Часов в плане": round(r.get("hours") or 0),
        "Задач": len(r.get("tasks") or []),
    } for r in rows])
    st.dataframe(table, hide_index=True, use_container_width=True)
    st.caption("Роль — строка без скобок; технология роли — в скобках. «Зависим от других "
               "команд» — в команде носителей нет, планировщик привлекает людей извне.")
