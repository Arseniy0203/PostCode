"""
Точка входа Streamlit-приложения сервиса PI-планирования.

Навигация между страницами через sidebar, поддержка режимов Baseline/Current
и безопасная загрузка данных с пересчетом факта.
"""

from __future__ import annotations

import os
import re
import sys
import copy
from io import BytesIO
from typing import Any

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import streamlit as st

from backend.starmap.bus_factor import starmap_summary
from backend.recalculation.recalc import apply_sprint_fact
from backend.kpi.metrics import kpi_breakdown
from backend.recalculation.pipeline import (
    dataset_from_parsed,
    fact_from_dataset_excel,
    week0_from_excel,
)

from frontend.theme import (PAGES, PAGE_LABELS, icon, inject_custom_css, metric_card,
                   render_brand, render_header, render_welcome, render_footer)
from views.dashboard_view import render_dashboard_view
from views.plan_view import render_plan_view
from views.risk_view import render_risk_view
from views.star_map_view import render_star_map_view
from views.overload_view import render_overload_view


APP_TITLE = os.getenv("APP_TITLE", "ПочтаТех: PI-Планирование")
TEMPLATE_PATH = os.getenv("TEMPLATE_PATH", os.path.join("data", "template",
                                                        "Шаблон_датасета.xlsx"))
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _looks_like_fact(name: str) -> bool:
    """Имя вида Sprint_2_Fact.xlsx: файл факта спринта, а не мастер-датасет."""
    return bool(re.search(r"(?:sprint|спринт)[ _-]*\d", name or "", flags=re.IGNORECASE))


def section_label(text: str) -> None:
    """Надзаголовок раздела мелкими прописными — вместо эмодзи-иконок."""
    st.markdown(f'<div class="section-label">{text}</div>', unsafe_allow_html=True)


@st.cache_data(show_spinner="Сборка Baseline и проверка рисков...")
def process_baseline(file_bytes: bytes,
                     defer: tuple[tuple[str, str], ...] = ()) -> dict[str, Any]:
    """Парсит исходный датасет и строит Baseline тем же конвейером, что и пересчёт.

    Задачи в работе планируются на остаток (смета − Spent_time_roles), ёмкость
    команды — средний velocity × 0.8 (backend.recalculation.pipeline.week0_from_excel).
    defer — снятые вручную задачи (кортеж пар, чтобы работал кэш).
    """
    state = week0_from_excel(file_bytes, defer=dict(defer) or None)
    result = {key: state[key] for key in ("parsed", "clean_deps", "plan", "alerts", "starmap")}
    result["data_quality"] = list((state.get("report") or {}).get("data_quality") or [])
    result["overload"] = state.get("overload")
    return result


@st.cache_data(show_spinner="Применение факта и пересчет текущего плана...")
def process_fact(prev_plan: dict[str, Any], master_deps: list[dict[str, Any]], master_engineers: list[dict[str, Any]],
                 master_estimates: list[dict[str, Any]], master_history: list[dict[str, Any]],
                 fact_file_bytes: bytes) -> dict[str, Any]:
    """Обрабатывает факт, обновляет Plan.current на основе предыдущего плана и пересчитывает риски.

    Файл факта — датасет с НАКОПЛЕННЫМИ часами (как в demo_scenarios): уже учтённое
    вычитается, в пересчёт идут часы именно за этот спринт.
    """
    fact = fact_from_dataset_excel(fact_file_bytes, prev_plan)
    capacity = prev_plan.get("capacity") or dataset_from_parsed(
        {"team_history": master_history, "engineers_profiles": master_engineers})["capacity"]
    updated_plan = apply_sprint_fact(
        plan=copy.deepcopy(prev_plan),
        fact=fact,
        dependencies=master_deps,
        engineers=master_engineers,
        capacity=capacity,
    )
    return {
        "plan": updated_plan,
        "alerts": [a.to_dict() for a in updated_plan.get("alerts") or []],
        "starmap": starmap_summary(updated_plan, master_engineers, master_estimates),
    }


def init_session_state() -> None:
    """Инициализирует State Machine для 6 спринтов."""
    if "calculated_plans" not in st.session_state:
        st.session_state.calculated_plans = {}  # Ключи: "baseline", 1, 2, ..., 6
        st.session_state.master_dataset = None  # Сырые данные Недели 0
    st.session_state.setdefault("fact_files", {})     # спринт → (имя файла, байты)
    st.session_state.setdefault("uploader_nonce", 0)  # новый ключ = пустой загрузчик


def _reset_uploaders() -> None:
    """Streamlit хранит файл в загрузчике между перезапусками: без смены ключа
    тот же файл обрабатывался бы снова и снова (бесконечный пересчёт)."""
    st.session_state.uploader_nonce = st.session_state.get("uploader_nonce", 0) + 1


def _recalculate_from(sprint: int, master: dict[str, Any]) -> tuple[int, str | None]:
    """Пересчитывает спринты начиная с sprint по сохранённым файлам факта.

    Returns:
        (последний успешно пересчитанный спринт, текст ошибки или None).
    """
    plans = st.session_state.calculated_plans
    files = st.session_state.fact_files
    for j in range(sprint, 7):
        plans.pop(j, None)
    last = sprint - 1
    for j in range(sprint, 7):
        if j not in files:
            break
        prev_plan = plans["baseline"]["plan"] if j == 1 else plans[j - 1]["plan"]
        try:
            plans[j] = process_fact(prev_plan, master["clean_deps"],
                                    master["parsed"]["engineers_profiles"], master["parsed"]["estimates"],
                                    master["parsed"]["team_history"], files[j][1])
        except Exception as exc:  # noqa: BLE001 — показываем пользователю, не роняем страницу
            name = files[j][0]
            for k in range(j, 7):
                files.pop(k, None)
            return last, f"Спринт {j} ({name}): {exc}"
        last = j
    return last, None


def _file_sprint_mismatch(name: str, sprint: int) -> str | None:
    """Предупреждение, если в имени файла явно другой номер спринта (Sprint_3 в слот 2)."""
    match = re.search(r"(?:sprint|спринт)[ _-]*(\d+)", name, flags=re.IGNORECASE)
    if match and int(match.group(1)) != sprint:
        return (f"Похоже, это файл спринта {match.group(1)}, а загружается спринт {sprint}. "
                "Проверьте файл.")
    return None


def manual_defer() -> dict[str, str]:
    """Задачи, снятые с квартала вручную: {task_id: комментарий}."""
    return st.session_state.setdefault("manual_defer", {})


def _rebuild_with_manual() -> None:
    """Пересобирает план после ручного решения: Неделя 0 заново, затем факты.

    Снятая задача не планируется вовсе, её ёмкость достаётся остальным, поэтому
    пересчитывать нужно с Недели 0, а не править готовый план.
    """
    master_bytes = st.session_state.get("master_bytes")
    if not master_bytes:
        st.session_state.fact_message = ("warning", "Чтобы менять план вручную, загрузите "
                                                    "мастер-датасет заново.")
        return
    defer = tuple(sorted(manual_defer().items()))
    base_state = process_baseline(master_bytes, defer)
    st.session_state.master_dataset = base_state
    st.session_state.calculated_plans["baseline"] = {
        "plan": base_state["plan"],
        "alerts": base_state["alerts"],
        "starmap": base_state["starmap"],
    }
    _recalculate_from(1, base_state)


def _fact_problem(name: str, data: bytes, sprint: int) -> str | None:
    """Что не так с файлом факта: чужой спринт, мастер-датасет или дубль.

    Все три случая — следствие похожести файлов: факты спринтов сделаны в том же
    формате, что и мастер-датасет, поэтому перепутать их легко.
    """
    if data and data == st.session_state.get("master_bytes"):
        return ("Это мастер-датасет Недели 0, а не факт спринта: файл совпадает с тем, "
                "что уже загружен как исходный план.")
    for loaded_sprint, (loaded_name, loaded_data) in st.session_state.get("fact_files", {}).items():
        if loaded_sprint != sprint and data and data == loaded_data:
            return (f"Этот файл уже загружен как факт спринта {loaded_sprint} "
                    f"(«{loaded_name}»): в нём нет работы за спринт {sprint}.")
    return _file_sprint_mismatch(name, sprint)


def _accept_fact(sprint: int, uploaded: Any, master: dict[str, Any], confirmed: bool = False) -> None:
    """Сохраняет файл спринта и пересчитывает его и все следующие загруженные спринты.

    Подозрительный файл (чужой спринт, мастер-датасет, дубль) не применяется
    без подтверждения.
    """
    name, data = (uploaded if isinstance(uploaded, tuple) else (uploaded.name, uploaded.getvalue()))
    if not confirmed and _fact_problem(name, data, sprint):
        st.session_state.pending_fact = (sprint, name, data)
        _reset_uploaders()
        st.rerun()
    st.session_state.pop("pending_fact", None)
    st.session_state.fact_files[sprint] = (name, data)
    warning = None
    later = [j for j in st.session_state.fact_files if j > sprint]
    last, error = _recalculate_from(sprint, master)
    if error:
        message = ("error", f"Не удалось пересчитать план. {error}")
    elif later and last > sprint:
        message = ("success", f"Спринт {sprint} заменён; спринты {sprint + 1}–{last} "
                              "пересчитаны заново по уже загруженным файлам.")
    else:
        message = ("success", f"Факт спринта {sprint} учтён, план пересчитан.")
    st.session_state.fact_message = message
    st.session_state.pop(f"replace_{sprint}", None)
    _reset_uploaders()
    st.rerun()


def render_fact_uploads(master: dict[str, Any]) -> None:
    """Сайдбар: загруженные спринты с кнопками «Заменить» / «Удалить» и загрузчик следующего."""
    plans = st.session_state.calculated_plans
    files = st.session_state.fact_files
    nonce = st.session_state.uploader_nonce

    message = st.session_state.pop("fact_message", None)
    if message:
        {"error": st.sidebar.error, "warning": st.sidebar.warning}.get(
            message[0], st.sidebar.success)(message[1])

    pending = st.session_state.get("pending_fact")
    if pending:
        sprint, name, data = pending
        st.sidebar.warning(f"{_fact_problem(name, data, sprint)} Файл «{name}» пока не применён.")
        col_yes, col_no = st.sidebar.columns(2)
        if col_yes.button("Всё равно загрузить", key="pending_yes", width="stretch"):
            _accept_fact(sprint, (name, data), master, confirmed=True)
        if col_no.button("Отмена", key="pending_no", width="stretch"):
            st.session_state.pop("pending_fact", None)
            st.rerun()

    for i in range(1, 7):
        if i in plans:
            name = files.get(i, ("файл загружен",))[0]
            with st.sidebar.expander(f"Спринт {i} · факт загружен", expanded=False):
                st.caption(name)
                col_replace, col_delete = st.columns(2)
                if col_replace.button("Заменить", key=f"replace_btn_{i}", width="stretch",
                                      help="Загрузить другой файл факта за этот спринт"):
                    st.session_state[f"replace_{i}"] = not st.session_state.get(f"replace_{i}", False)
                    st.rerun()
                if col_delete.button("Удалить", key=f"delete_btn_{i}", width="stretch",
                                     help="Убрать факт этого спринта и всех следующих"):
                    for j in range(i, 7):
                        plans.pop(j, None)
                        files.pop(j, None)
                    last_text = f"спринта {i}" if i == 6 else f"спринтов {i}–6"
                    st.session_state.fact_message = (
                        "success", f"Факт {last_text} удалён. Загрузите спринт {i} заново.")
                    _reset_uploaders()
                    st.rerun()
                if st.session_state.get(f"replace_{i}"):
                    later = [j for j in plans if isinstance(j, int) and j > i]
                    if later:
                        st.caption(f"Спринты {i + 1}–{max(later)} пересчитаются заново "
                                   "по уже загруженным файлам.")
                    new_fact = st.file_uploader(f"Новый файл факта спринта {i}", type=["xlsx"],
                                                key=f"reupload_fact_{i}_{nonce}")
                    if new_fact:
                        _accept_fact(i, new_fact, master)
        else:
            # Разблокируем только следующий по порядку спринт
            fact_file = st.sidebar.file_uploader(
                f"Загрузить факт Спринта {i}",
                type=["xlsx"],
                key=f"upload_fact_{i}_{nonce}",
                help="Файл в формате датасета с накопленными часами — как в demo_scenarios/.",
            )
            if fact_file:
                _accept_fact(i, fact_file, master)
            break


def render_kpi_widgets(baseline_tasks: list[dict[str, Any]], current_tasks: list[dict[str, Any]],
                       current_sprint: int = 0) -> None:
    """Отрисовывает виджеты KPI в шапке приложения.

    current_sprint — последний спринт с загруженным фактом (0 — Неделя 0): Say/Do
    показывается за него, PI до конца квартала — прогнозом по текущему плану.
    """
    if not baseline_tasks:
        return
    current_sprint = int(current_sprint or 0)
    metrics = kpi_breakdown(baseline_tasks, current_tasks, current_sprint=current_sprint)
    quarter_over = current_sprint >= 6
    pi = metrics["pi_predictability"]["fact" if quarter_over else "forecast"]
    pi_value = pi["value"]
    pi_norm = metrics["norms"]["pi_predictability"]
    latest = next((row for row in metrics["say_do"] if row["sprint"] == current_sprint), None)
    included = [t for t in current_tasks if t.get("decision") == "included"]
    teams = {t.get("team_id") for t in included if t.get("team_id")}
    done = sum(t.get("status") == "Done" for t in included)
    cols = st.columns(4)
    with cols[0]:
        metric_card("Задачи в квартале", str(len(included)), f"{done} выполнено из {len(included)}", "plan")
    with cols[1]:
        metric_card("PI Predictability" + ("" if quarter_over else " · прогноз"),
                    "—" if pi.get("status") == "no_data" else f"{pi_value:.1f}%",
                    f"Норма {pi_norm[0]:g}–{pi_norm[1]:g}%", "chart",
                    "success" if pi_norm[0] <= pi_value <= pi_norm[1] else "warning")
    with cols[2]:
        has_fact = latest is not None and latest.get("status") not in ("future", "no_data")
        metric_card("Say/Do Ratio", f"{latest['value']:.1f}%" if has_fact else "—",
                    f"Спринт {current_sprint} · норма 90–105%" if has_fact else "Ожидаем факт спринта", "check",
                    ("success" if latest["status"] == "ok" else "warning") if has_fact else "muted")
    with cols[3]:
        metric_card("Команды в плане", str(len(teams)), f"Учтено {current_sprint} из 6 спринтов", "people")


def main() -> None:
    """Инициализирует Streamlit-страницу, State Machine и сайдбар-навигацию."""
    st.set_page_config(page_title=APP_TITLE, page_icon="◈", layout="wide")
    inject_custom_css()
    init_session_state()

    with st.sidebar:
        render_brand()
        section_label("Рабочее пространство")
        with st.container(key="sidebar-nav"):
            page = st.radio("Раздел", options=PAGES, format_func=PAGE_LABELS.get,
                            key="navigation", label_visibility="collapsed")

    # СОСТОЯНИЕ 0: Мастер-датасет не загружен
    if "baseline" not in st.session_state.calculated_plans:
        render_header(page, "Начало работы · Неделя 0")
        uploaded_file = render_welcome()
        demo_path = os.path.join(project_root, "data", "raw", "Хакатон_датасетс_правками_по_списку_вопросов_1.xlsx")
        # Обе кнопки рядом с загрузчиком: нечего загружать — возьмите шаблон или
        # посмотрите на демо-данных
        col_template, col_demo = st.columns(2)
        demo_requested = False
        if os.path.exists(TEMPLATE_PATH):
            with open(TEMPLATE_PATH, "rb") as template:
                col_template.download_button(
                    "Скачать шаблон датасета", data=template.read(),
                    file_name=os.path.basename(TEMPLATE_PATH), mime=XLSX_MIME,
                    help="Пустой файл с нужными таблицами и колонками: заполните своими "
                         "данными и загрузите обратно.",
                    width="stretch")
        if os.path.exists(demo_path):
            demo_requested = col_demo.button("Открыть пример на демо-данных", key="load_demo",
                                             width="stretch")
        if demo_requested:
            with open(demo_path, "rb") as demo_file:
                uploaded_file = BytesIO(demo_file.read())
                uploaded_file.name = os.path.basename(demo_path)
        with st.sidebar:
            st.html(f'<div class="soft-sidebar-note">{icon("file")}<strong>Начните с данных</strong>'
                    '<p>Загрузите мастер-датасет, чтобы открыть план, риски и карту компетенций.</p></div>')

        if uploaded_file and _looks_like_fact(uploaded_file.name) and not st.session_state.get(
                "master_confirmed"):
            st.warning(f"Файл «{uploaded_file.name}» похож на факт спринта, а не на мастер-датасет "
                       "Недели 0. С него план квартала начнётся так, будто работа уже выполнена.")
            if st.button("Всё равно загрузить как мастер-датасет"):
                st.session_state.master_confirmed = True
                st.rerun()
            return

        if uploaded_file:
            st.session_state.pop("master_confirmed", None)
            try:
                base_state = process_baseline(uploaded_file.getvalue())
                st.session_state.master_dataset = base_state
                st.session_state.master_name = uploaded_file.name
                st.session_state.master_bytes = uploaded_file.getvalue()
                st.session_state.fact_files = {}
                st.session_state.calculated_plans["baseline"] = {
                    "plan": base_state["plan"],
                    "alerts": base_state["alerts"],
                    "starmap": base_state["starmap"]
                }
                st.rerun()
            except Exception as e:
                # ValueError парсера — понятная причина по-русски; прочее — общий текст
                reason = str(e) if isinstance(e, ValueError) and str(e) else                     "файл не похож на датасет в формате организаторов."
                st.error(f"Не удалось прочитать мастер-датасет: {reason}")
                with st.expander("Технические детали"):
                    st.code(f"{type(e).__name__}: {e}")
        render_footer()
        return  # Полная блокировка остального UI до загрузки Мастера

    # СОСТОЯНИЕ 1-6: Пайплайн загрузки спринтов в Сайдбаре
    with st.sidebar:
        section_label("Данные квартала")

    with st.sidebar.expander("Мастер-датасет · Неделя 0", expanded=False):
        st.caption(st.session_state.get("master_name") or "Датасет загружен.")
        if st.button("Сбросить весь план и начать заново", width="stretch"):
            st.session_state.calculated_plans = {}
            st.session_state.master_dataset = None
            st.session_state.fact_files = {}
            st.session_state.manual_defer = {}
            _reset_uploaders()
            st.rerun()

    render_fact_uploads(st.session_state.master_dataset)

    st.sidebar.markdown("---")

    # НАВИГАЦИЯ (Выбор текущего хронологического среза)
    with st.sidebar:
        section_label("Режим просмотра")

    options = {"baseline": "Снимок (Baseline Неделя 0)"}
    for i in range(1, 7):
        if i in st.session_state.calculated_plans:
            options[i] = f"После Спринта {i}"

    selected_mode_label = st.sidebar.radio(
        "Текущий срез данных",
        options=list(options.values()),
        index=len(options) - 1  # По умолчанию выбираем самый свежий загруженный срез
    )

    selected_key = "baseline"
    for k, v in options.items():
        if v == selected_mode_label:
            selected_key = k
            break

    with st.sidebar:
        st.html(f'<div class="soft-sidebar-note">{icon("plan")}<strong>Каждый спринт — шаг к цели</strong>'
                '<p>Загружайте факт каждые две недели. План и риски пересчитаются автоматически.</p></div>')

    # РЕНДЕР ИНТЕРФЕЙСА
    active_state = st.session_state.calculated_plans[selected_key]
    plan_dict = active_state["plan"]
    alerts = active_state["alerts"]
    starmap = active_state["starmap"]

    render_header(page, selected_mode_label)
    render_kpi_widgets(plan_dict.get("baseline", []), plan_dict.get("current", []),
                       plan_dict.get("current_sprint", 0))
    if page == "Обзор":
        render_dashboard_view(plan_dict, alerts, starmap)
    elif page == "План (PI-цикл)":
        def on_manual_change() -> None:
            """Снятие задачи меняет весь план, поэтому пересобираем и перерисовываем."""
            _rebuild_with_manual()
            st.rerun()

        render_plan_view(plan_dict, selected_mode_label, alerts, starmap, on_manual_change)
    elif page == "Всё сразу или по плану":
        render_overload_view((st.session_state.master_dataset or {}).get("overload"))
    elif page == "Риски и Алерты":
        render_risk_view(alerts, plan_dict.get("resolved_alerts") or [],
                         (st.session_state.master_dataset or {}).get("data_quality") or [])
    elif page == "Звёздная карта":
        render_star_map_view(starmap)
    render_footer()


if __name__ == "__main__":
    main()
