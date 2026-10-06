"""Soft UI Dashboard presentation helpers; no planning state lives here."""

from base64 import b64encode
from functools import lru_cache
from html import escape
from pathlib import Path

import streamlit as st
from streamlit.runtime.uploaded_file_manager import UploadedFile

ASSETS = Path(__file__).resolve().parent / "assets"
PAGES = ["Титульная", "Обзор", "План (PI-цикл)", "Показатели", "Всё сразу или по плану", "Риски и Алерты", "Звёздная карта"]
PAGE_LABELS = {
    "Титульная": "Главная",
    "Показатели": "Показатели KPI",
    "Обзор": "Обзор квартала",
    "План (PI-цикл)": "Квартальный план",
    "Всё сразу или по плану": "Загрузка команд",
    "Риски и Алерты": "Риски и алерты",
    "Звёздная карта": "Звёздная карта",
}
ICONS = {
    "dashboard": '<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/>',
    "plan": '<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M7 3v4m10-4v4M3 11h18m-13 4h2m4 0h2m-8 3h2"/>',
    "chart": '<path d="M4 20V10m8 10V4m8 16v-7"/>',
    "risk": '<path d="m12 3 10 18H2L12 3Zm0 6v5m0 3v.1"/>',
    "people": '<circle cx="9" cy="8" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3m1-16a3 3 0 0 1 0 6m3 10v-3a6 6 0 0 0-2-4"/>',
    "check": '<path d="m5 12 4 4L19 6"/>',
    "arrow": '<path d="M4 12h16m-6-6 6 6-6 6"/>',
    "file": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Zm0 0v6h6M8 13h8m-8 4h5"/>',
}


def icon(name: str) -> str:
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" '
           'stroke="white" stroke-width="1.8" stroke-linecap="round" '
           f'stroke-linejoin="round">{ICONS[name]}</svg>')
    return f'<img src="data:image/svg+xml;base64,{b64encode(svg.encode()).decode()}" width="22" height="22" alt="" />'


@lru_cache(maxsize=8)
def asset_uri(name: str) -> str:
    path = ASSETS / "soft-ui" / name
    mime = "image/png" if path.suffix == ".png" else "image/jpeg"
    return f"data:{mime};base64,{b64encode(path.read_bytes()).decode()}"


def inject_custom_css() -> None:
    st.html(f"<style>{(ASSETS / 'soft-ui.css').read_text(encoding='utf-8')}</style>")


def navigate(page: str, section: str | None = None) -> None:
    # Applied before the sidebar widget is built, on the next run.
    st.session_state["next_page"] = page
    if section is not None:
        st.session_state["kpi_section"] = section


def render_brand() -> None:
    logo = b64encode((ASSETS / "logo_pochtatex.png").read_bytes()).decode()
    st.html('<div class="soft-brand"><div class="soft-brand-logo">'
            f'<img src="data:image/png;base64,{logo}" alt="ПочтаТех" /></div>'
            '<span class="soft-brand-caption">Квартальное PI-планирование</span></div>')


def render_header(page: str, snapshot: str) -> None:
    title = PAGE_LABELS.get(page, page)
    st.html(f'<div class="soft-topbar"><div><div class="soft-breadcrumb">'
            f'Рабочее пространство <span>/</span> {escape(title)}</div>'
            f'<strong>{escape(title)}</strong></div><div class="soft-snapshot">'
            f'<span class="soft-status-dot"></span>{escape(snapshot)}</div></div>')


def metric_card(label: str, value: str, note: str, symbol: str, tone: str = "muted",
                page: str | None = None, section: str | None = None) -> None:
    with st.container(key=f"metric-{symbol}"):
        content, action = st.columns([4, 1], gap="small")
        decoration = "" if page else f'<span class="soft-metric-icon">{icon(symbol)}</span>'
        content.html(f'<article class="soft-metric"><div><p>{escape(label)}</p>'
                f'<strong>{escape(value)}</strong><small class="soft-{tone}">{escape(note)}</small>'
                f'</div>{decoration}</article>')
        if page:
            symbols = {"plan": "calendar_month", "chart": "bar_chart", "check": "done", "people": "groups"}
            action.button(f"Открыть: {label}", icon=f":material/{symbols[symbol]}:",
                      key=f"kpi-{symbol}", help=f"Открыть: {label}", type="primary",
                      on_click=navigate, args=(page, section))


def render_title(ready: bool = False, demo: bool = False) -> None:
    st.html(f'<div class="soft-hero"><div class="soft-hero-copy">'
            '<p class="soft-eyebrow">ПОЧТАТЕХ / PI-ПЛАНИРОВАНИЕ</p>'
            '<h1>План квартала<br>и возможности команды</h1>'
            '<p>Распределяйте задачи по спринтам, проверяйте загрузку и отслеживайте '
            'риски. Карта компетенций поможет найти дефицит специалистов.</p>'
            '<span class="soft-pill">6 спринтов · 12 недель</span></div>'
            f'<div class="soft-rocket"><img src="{asset_uri("rocket-white.png")}" alt="" /></div></div>')
    if ready:
        st.caption("Демо-сценарий: все спринты уже рассчитаны." if demo else "Датасет загружен. Рабочее пространство готово.")
        left, right = st.columns(2)
        left.button("Открыть обзор квартала", key="title_overview", type="primary",
                    on_click=navigate, args=("Обзор",), width="stretch")
        right.button("Открыть квартальный план", key="title_plan",
                     on_click=navigate, args=("План (PI-цикл)",), width="stretch")
        st.html('<div class="detail-grid"><section class="detail-cell"><span>Планирование</span>'
                '<p>Задачи, зависимости и распределение по шести спринтам.</p></section>'
                '<section class="detail-cell"><span>Контроль выполнения</span><p>Сравнение исходного плана с фактом и прогнозом.</p></section>'
                '<section class="detail-cell"><span>Ресурсы и риски</span><p>Загрузка команд, нехватка часов и сдвиги сроков.</p></section>'
                '<section class="detail-cell"><span>Компетенции</span><p>Карта инженеров и рекомендации по найму и обучению.</p></section></div>')


def render_welcome() -> UploadedFile | None:
    render_title()
    left, right = st.columns([1.5, 1], gap="large")
    with left, st.container(border=True, key="soft-card-upload"):
        st.subheader("Создайте план квартала")
        st.caption("Загрузите Excel с задачами, оценками и профилями инженеров. Мы построим исходный план и проверим ресурсы.")
        uploaded = st.file_uploader(
            "Мастер-датасет Недели 0 (.xlsx)", type=["xlsx"],
            help="Файл инициализации плана. Служит основой для расчета Baseline и KPI.",
        )
        st.caption("После загрузки можно добавлять факты спринтов и сравнивать результат с исходным планом.")
    with right:
        st.html('<div class="soft-card soft-steps"><h3>От данных к решениям</h3>'
                '<div><b>01</b><section><strong>Загрузите исходные данные</strong><p>Зафиксируйте обязательства на Неделе 0.</p></section></div>'
                '<div><b>02</b><section><strong>Следите за спринтами</strong><p>Добавляйте факт и получайте обновлённый план.</p></section></div>'
                '<div><b>03</b><section><strong>Управляйте рисками</strong><p>Находите дефицит ресурсов и развивайте команду.</p></section></div></div>')
    return uploaded


def render_footer() -> None:
    st.html('<div class="soft-footer"><span>ПочтаТех · Кронверкские псы</span>'
            '<span>PI-планирование <span aria-hidden="true"> / </span> Soft UI Dashboard</span></div>')
