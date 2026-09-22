# Ответственный: Человек 4 (frontend + tests + docs)
"""Страница списка алертов (рисков)."""

from __future__ import annotations

from typing import Any
import streamlit as st

from .labels import sprint_moment

# Подтипы алертов — заголовки групп на вкладке «Наблюдения» и подписи карточек
CATEGORY_LABELS = {
    "critical": "Срыв инициативы в квартале",
    "cascade": "Каскадный сдвиг",
    "hiring": "Роли нет в компании",
    "resource": "Дефицит часов роли",
    "capacity": "Не хватает ёмкости",
    "single_point": "Единственный носитель роли",
    "blocked": "Блокировки",
    "stale": "Зависшие задачи",
    "overrun": "Перерасход сметы",
    "kpi": "KPI вне нормы",
    "data_quality": "Качество данных",
}
ROLE_CATEGORIES = {"hiring", "resource", "single_point"}


def _title(alert: dict[str, Any]) -> str:
    """Заголовок карточки: роль — для кадровых алертов, иначе задача и инициатива."""
    category = alert.get("category")
    if category in ROLE_CATEGORIES and alert.get("role"):
        head = f"Роль «{alert['role']}»"
    else:
        parts = [alert.get("task_id"), alert.get("prodf_id_at_risk")]
        head = " · ".join(p for p in parts if p) or "Весь план"
    label = CATEGORY_LABELS.get(category)
    return f"**{head}**" + (f" — {label.lower()}" if label else "")


def _render_alert_card(alert: dict[str, Any], render_func: Any) -> None:
    moment = sprint_moment(alert.get("sprint_detected"))
    since = alert.get("since_sprint")
    if alert.get("status") == "ongoing" and since is not None:
        moment = "держится с Недели 0" if not since else f"держится со спринта {since}"
    text = f"{_title(alert)}\n\n{alert.get('description', '')}"
    if alert.get("recommendation"):
        text += f"\n\n**Что сделать:** {alert['recommendation']}"
    text += f"\n\n:gray[{moment}]"
    render_func(text)


def _render_list(alerts: list[dict[str, Any]], render_func: Any, empty: str) -> None:
    if not alerts:
        st.caption(empty)
    for alert in alerts:
        _render_alert_card(alert, render_func)


def _render_grouped(alerts: list[dict[str, Any]], empty: str) -> None:
    """Наблюдения свёрнуты по подтипам, чтобы не было длинной простыни."""
    if not alerts:
        st.caption(empty)
        return
    groups: dict[str, list[dict[str, Any]]] = {}
    for alert in alerts:
        groups.setdefault(CATEGORY_LABELS.get(alert.get("category"), "Прочее"), []).append(alert)
    for label, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        with st.expander(f"{label} · {len(items)}"):
            for alert in items:
                text = alert.get("description", "")
                if alert.get("recommendation"):
                    text += f"\n\n**Что сделать:** {alert['recommendation']}"
                st.markdown(text)
                st.markdown("---")


def render_risk_view(alerts: list[Any], resolved: list[Any] | None = None,
                     data_quality: list[Any] | None = None) -> None:
    """Отрисовывает список алертов с привязкой к PRODF-цели.

    Args:
        alerts: список Alert (см. contracts/alert_schema.md), каждый содержит
            prodf_id_at_risk для привязки к PRODF-цели.
        resolved: алерты, решённые с прошлого пересчёта.
        data_quality: отчёт о качестве исходного датасета (Неделя 0).
    """
    st.header("Риски и алерты")

    alerts = [a for a in alerts or [] if isinstance(a, dict)]
    red_alerts = [a for a in alerts if a.get("type") == "red"]
    yellow_alerts = [a for a in alerts if a.get("type") == "yellow"]
    orange_alerts = [a for a in alerts if a.get("type") == "orange"]
    info_alerts = [a for a in alerts if a.get("type") == "info"]
    data_quality = [a for a in data_quality or [] if isinstance(a, dict)]

    affected_prodfs = {a.get("prodf_id_at_risk") for a in red_alerts + yellow_alerts
                       if a.get("prodf_id_at_risk")}

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Критические", len(red_alerts))
    col2.metric("Каскадные", len(yellow_alerts))
    col3.metric("Ресурсные", len(orange_alerts))
    col4.metric("Инициативы под риском", len(affected_prodfs))

    if not red_alerts and not yellow_alerts:
        st.success("Срывов и каскадных сдвигов нет: инициативы укладываются в квартал.")

    tabs = st.tabs([
        f"Критические · {len(red_alerts)}",
        f"Каскадные · {len(yellow_alerts)}",
        f"Ресурсные · {len(orange_alerts)}",
        f"Наблюдения · {len(info_alerts)}",
        f"Качество данных · {len(data_quality)}",
    ])

    with tabs[0]:
        st.caption("Прогноз окончания за 6-м спринтом или задача выпала из плана Недели 0.")
        _render_list(red_alerts, st.error, "Критических рисков нет.")
    with tabs[1]:
        st.caption("Задача позже плана Недели 0 и сдвигает зависимые от неё задачи.")
        _render_list(yellow_alerts, st.warning, "Каскадных сдвигов нет.")
    with tabs[2]:
        st.caption("Роли нет в компании или её часов не хватает на спринт.")
        _render_list(orange_alerts, st.info, "Ресурсных дефицитов нет.")
    with tabs[3]:
        st.caption("Не срывают план сейчас, но стоит знать: единственные носители ролей, "
                   "зависшие задачи, перерасход сметы.")
        _render_grouped(info_alerts, "Наблюдений нет.")
    with tabs[4]:
        st.caption("Проблемы исходного датасета: задачи-призраки, дубли связей, расхождения "
                   "сметы. Решение не выдумываем — показываем, что исправить в данных.")
        _render_grouped(data_quality, "Проблем в данных не найдено.")

    resolved = [a for a in resolved or [] if isinstance(a, dict)]
    if resolved:
        with st.expander(f"Решено с прошлого пересчёта · {len(resolved)}"):
            for alert in resolved:
                st.markdown(f"{_title(alert)}\n\n{alert.get('description', '')}")
                st.markdown("---")
