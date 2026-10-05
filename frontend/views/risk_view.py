"""Страница списка алертов (рисков)."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from .components import detail_grid, styled_table
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

# Цвет полосы карточки и точки в легенде: один и тот же для вкладки, счётчика и карточки
TYPE_COLORS = {
    "red": "#B3261E",
    "yellow": "#9A6100",
    "orange": "#B4540F",
    "info": "#5B6472",
}


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


def _render_alert_card(alert: dict[str, Any], index: int) -> None:
    """Карточка с цветной полосой по типу риска.

    Встроенные st.error/st.warning/st.info дают только красный, жёлтый и синий —
    оранжевого среди них нет, и ресурсные алерты выглядели как подсказки.
    """
    moment = sprint_moment(alert.get("sprint_detected"))
    since = alert.get("since_sprint")
    if alert.get("status") == "ongoing" and since is not None:
        moment = "держится с Недели 0" if not since else f"держится со спринта {since}"
    alert_type = alert.get("type") if alert.get("type") in TYPE_COLORS else "info"
    with st.container(key=f"alert-{alert_type}-{alert.get('category')}-{index}"):
        st.markdown(_title(alert))
        detail_grid([("Что происходит", alert.get("description", "")),
                     ("Что сделать", alert.get("recommendation")), ("Период", moment)],
                    tone="danger" if alert_type == "red" else "warning" if alert_type in ("yellow", "orange") else "neutral")


def _render_list(alerts: list[dict[str, Any]], empty: str) -> None:
    if not alerts:
        st.caption(empty)
    for index, alert in enumerate(alerts):
        _render_alert_card(alert, index)


# Наблюдения: сверху то, что требует решения (единственный носитель, перерасход),
# снизу — справочное. Сортировать по размеру группы смысла нет.
INFO_ORDER = ["single_point", "overrun", "blocked", "stale", "kpi", "data_quality"]


def _render_grouped(alerts: list[dict[str, Any]], empty: str) -> None:
    """Наблюдения свёрнуты по подтипам, чтобы не было длинной простыни."""
    if not alerts:
        st.caption(empty)
        return
    groups: dict[str, list[dict[str, Any]]] = {}
    weight: dict[str, int] = {}
    for alert in alerts:
        category = alert.get("category")
        label = CATEGORY_LABELS.get(category, "Прочее")
        groups.setdefault(label, []).append(alert)
        weight[label] = INFO_ORDER.index(category) if category in INFO_ORDER else len(INFO_ORDER)
    for label, items in sorted(groups.items(), key=lambda kv: (weight[kv[0]], -len(kv[1]))):
        with st.expander(f"{label} · {len(items)}"):
            for alert in items:
                detail_grid([("Наблюдение", alert.get("description", "")),
                             ("Что сделать", alert.get("recommendation"))])


def _render_data_quality(items: list[dict[str, Any]]) -> None:
    """Таблица вместо карточек: записей много, и все однотипные."""
    if not items:
        st.caption("Проблем в данных не найдено.")
        return
    table = pd.DataFrame([{
        "Задача": item.get("task_id") or "—",
        "Что не так": item.get("description", ""),
        "Что сделать": item.get("recommendation", ""),
    } for item in items])
    styled_table(table)
    st.caption(f"Записей: {len(items)}. Планировщик берёт за истину калькулятор сметы по ролям, "
               "а расхождение показывает здесь.")


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

    # Фильтр по команде: руководителю команды нужны только её риски.
    # Алерты без команды (общие по компании) показываем всегда.
    teams = sorted({a.get("team_id") for a in alerts if a.get("team_id")})
    if teams:
        picked_team = st.selectbox("Команда", options=["Все команды", *teams], key="risk_team")
        if picked_team != "Все команды":
            alerts = [a for a in alerts
                      if a.get("team_id") in (picked_team, None)
                      or picked_team in (a.get("affected_team_ids") or [])]
            red_alerts = [a for a in alerts if a.get("type") == "red"]
            yellow_alerts = [a for a in alerts if a.get("type") == "yellow"]
            orange_alerts = [a for a in alerts if a.get("type") == "orange"]
            info_alerts = [a for a in alerts if a.get("type") == "info"]

    affected_prodfs = {a.get("prodf_id_at_risk") for a in red_alerts + yellow_alerts
                       if a.get("prodf_id_at_risk")}

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Критические", len(red_alerts))
    col2.metric("Каскадные", len(yellow_alerts))
    col3.metric("Ресурсные", len(orange_alerts))
    col4.metric("Инициативы под риском", len(affected_prodfs))

    # Легенда: цвет точки совпадает с полосой карточки ниже
    st.markdown(
        "".join(
            f'<span class="legend-dot" style="background:{TYPE_COLORS[key]}"></span>{label}'
            for key, label in (("red", "срыв квартала"), ("yellow", "сдвиг цепочки"),
                               ("orange", "нехватка людей"), ("info", "наблюдение"))
        ),
        unsafe_allow_html=True,
    )

    if not red_alerts and not yellow_alerts:
        st.success("Срывов и каскадных сдвигов нет: инициативы укладываются в квартал.")

    names = [
        f"Критические · {len(red_alerts)}",
        f"Каскадные · {len(yellow_alerts)}",
        f"Ресурсные · {len(orange_alerts)}",
        f"Наблюдения · {len(info_alerts)}",
        f"Качество данных · {len(data_quality)}",
    ]
    counts = [len(red_alerts), len(yellow_alerts), len(orange_alerts), len(info_alerts),
              len(data_quality)]
    tabs = st.tabs(names, default=next((n for n, c in zip(names, counts) if c), names[0]))

    with tabs[0]:
        st.caption("Прогноз окончания за 6-м спринтом или задача выпала из плана Недели 0.")
        _render_list(red_alerts, "Критических рисков нет.")
    with tabs[1]:
        st.caption("Задача позже плана Недели 0 и сдвигает зависимые от неё задачи.")
        _render_list(yellow_alerts, "Каскадных сдвигов нет.")
    with tabs[2]:
        st.caption("Роли нет в компании или её часов не хватает на спринт.")
        _render_list(orange_alerts, "Ресурсных дефицитов нет.")
    with tabs[3]:
        st.caption("Не срывают план сейчас, но стоит знать: единственные носители ролей, "
                   "зависшие задачи, перерасход сметы.")
        _render_grouped(info_alerts, "Наблюдений нет.")
    with tabs[4]:
        st.caption("Проблемы исходного датасета: задачи-призраки, дубли связей, расхождения "
                   "сметы. Решение не выдумываем — показываем, что исправить в данных.")
        _render_data_quality(data_quality)

    resolved = [a for a in resolved or [] if isinstance(a, dict)]
    if resolved:
        with st.expander(f"Решено с прошлого пересчёта · {len(resolved)}"):
            for alert in resolved:
                st.markdown(_title(alert))
                detail_grid([("Решено", alert.get("description", ""))], tone="success")
