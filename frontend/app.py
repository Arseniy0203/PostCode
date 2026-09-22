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
import tempfile
from typing import Any

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import streamlit as st

from backend.ingestion.parse_dataset import parse_excel, filter_orphan_dependencies
from backend.planning.scheduler import build_baseline_plan
from backend.risk.alerts import collect_alerts
from backend.starmap.bus_factor import starmap_summary
from backend.recalculation.recalc import apply_sprint_fact
from backend.kpi.metrics import kpi_breakdown
from backend.recalculation.baseline import save_baseline
from backend.recalculation.pipeline import (
    dataset_from_parsed,
    fact_from_dataset_excel,
    week0_from_excel,
)

from views.plan_view import render_plan_view
from views.risk_view import render_risk_view
from views.star_map_view import render_star_map_view
from views.overload_view import render_overload_view


APP_TITLE = os.getenv("APP_TITLE", "ПочтаТех: PI-Планирование")
LOGO_PATH = os.getenv("LOGO_PATH", os.path.join("frontend", "assets", "logo_pochtatex.png"))


def inject_custom_css() -> None:
    """Инжектирует фирменный стиль: светлая корпоративная тема ПочтаТеха.

    Принципы: белый фон, один акцентный цвет из логотипа (#294DA6), тонкие
    линии вместо теней и градиентов, табличные цифры в метриках и таблицах.
    """
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

        :root {
            --brand: #294DA6;          /* синий из логотипа «почтатех» */
            --brand-dark: #1D3876;
            --brand-tint: #EEF2FB;
            --ink: #111827;            /* основной текст */
            --ink-muted: #5B6472;      /* подписи и пояснения */
            --line: #E4E7EC;           /* границы и разделители */
            --surface: #FFFFFF;
            --surface-alt: #F7F8FA;    /* сайдбар, шапки таблиц */
            --danger: #B3261E;
            --warning: #9A6100;
            --success: #1B6D45;
        }

        #MainMenu, footer {visibility: hidden;}
        header {background: transparent !important;}

        html, body, .stApp, [class*="css"] {
            font-family: 'Inter', -apple-system, 'Segoe UI', sans-serif;
            font-feature-settings: 'tnum' 1, 'cv05' 1;
        }

        .stApp {
            background: var(--surface);
            color: var(--ink);
        }

        .block-container {
            padding-top: 2.75rem;
            padding-bottom: 4rem;
            max-width: 1360px;
        }

        /* --- Типографика: заголовки сдержанные, без «плакатности» --- */
        h1 {
            font-size: 1.75rem !important;
            font-weight: 600 !important;
            letter-spacing: -0.02em;
            color: var(--ink) !important;
            margin-bottom: 0.25rem !important;
        }
        h2 {
            font-size: 1.15rem !important;
            font-weight: 600 !important;
            color: var(--ink) !important;
            letter-spacing: -0.01em;
        }
        h3, h4, h5, h6 {
            font-size: 0.95rem !important;
            font-weight: 600 !important;
            color: var(--ink) !important;
        }
        .stApp p, .stApp li, .stApp span, .stApp label, .stApp div {
            color: var(--ink);
        }
        .stCaption, [data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {
            color: var(--ink-muted) !important;
            font-size: 0.8rem !important;
        }

        /* Надзаголовок раздела: мелкие прописные, как в отчётах */
        .section-label {
            font-size: 0.72rem;
            font-weight: 600;
            letter-spacing: 0.09em;
            text-transform: uppercase;
            color: var(--ink-muted);
            margin-bottom: 0.6rem;
        }

        /* --- Метрики: плоские карточки с тонкой рамкой --- */
        div[data-testid="stMetric"] {
            background: var(--surface);
            border: 1px solid var(--line);
            border-radius: 6px;
            padding: 1.1rem 1.25rem;
        }
        div[data-testid="stMetricLabel"] p {
            font-size: 0.78rem !important;
            font-weight: 500 !important;
            letter-spacing: 0.02em;
            color: var(--ink-muted) !important;
        }
        div[data-testid="stMetricValue"] {
            font-size: 2rem !important;
            font-weight: 600 !important;
            letter-spacing: -0.03em;
            color: var(--ink) !important;
        }
        div[data-testid="stMetricValue"] > div {color: var(--ink) !important;}
        div[data-testid="stMetricDelta"] {font-size: 0.78rem !important;}
        div[data-testid="stMetricDelta"] svg {display: none;}

        /* --- Контейнеры и раскрывающиеся блоки --- */
        div[data-testid="stExpander"],
        div[data-testid="stVerticalBlockBorderWrapper"] {
            background: var(--surface);
            border: 1px solid var(--line) !important;
            border-radius: 6px !important;
            box-shadow: none !important;
        }
        div[data-testid="stExpander"] summary {
            font-size: 0.88rem;
            font-weight: 500;
        }

        /* --- Сайдбар --- */
        [data-testid="stSidebar"] {
            background: var(--surface-alt) !important;
            border-right: 1px solid var(--line);
        }
        [data-testid="stSidebar"] .block-container {padding-top: 1.5rem;}
        [data-testid="stSidebar"] h1,
        [data-testid="stSidebar"] h2,
        [data-testid="stSidebar"] h3 {
            font-size: 0.72rem !important;
            font-weight: 600 !important;
            letter-spacing: 0.09em;
            text-transform: uppercase;
            color: var(--ink-muted) !important;
        }
        [data-testid="stSidebar"] p,
        [data-testid="stSidebar"] span,
        [data-testid="stSidebar"] label {color: var(--ink) !important;}

        /* --- Кнопки: один акцент, без градиентов --- */
        .stButton > button,
        [data-testid="stFileUploader"] button,
        [data-testid="stFormSubmitButton"] button {
            background: var(--brand) !important;
            color: #FFFFFF !important;
            border: 1px solid var(--brand) !important;
            border-radius: 5px !important;
            font-weight: 500 !important;
            font-size: 0.85rem !important;
            box-shadow: none !important;
            transition: background 0.15s ease;
        }
        .stButton > button *,
        [data-testid="stFileUploader"] button * {color: #FFFFFF !important; fill: #FFFFFF !important;}
        .stButton > button:hover,
        [data-testid="stFileUploader"] button:hover {
            background: var(--brand-dark) !important;
            border-color: var(--brand-dark) !important;
        }
        [data-testid="stTooltipIcon"] {background: transparent !important;}
        [data-testid="stTooltipIcon"] * {fill: var(--ink-muted) !important;}

        /* --- Загрузка файла --- */
        [data-testid="stFileUploader"] > section {
            background: var(--surface-alt) !important;
            border: 1px dashed var(--line) !important;
            border-radius: 6px !important;
            box-shadow: none !important;
        }
        [data-testid="stFileUploader"] > section:hover {border-color: var(--brand) !important;}
        [data-testid="stFileUploaderDropzoneInstructions"] span,
        [data-testid="stFileUploaderDropzoneInstructions"] small,
        [data-testid="stFileInfo"] p, [data-testid="stFileInfo"] small,
        [data-testid="stFileInfo"] span {color: var(--ink-muted) !important;}

        /* --- Таблицы: плотные, с тонкой сеткой --- */
        [data-testid="stDataFrame"] {border: 1px solid var(--line); border-radius: 6px;}
        [data-testid="stDataFrame"] * {font-size: 0.84rem;}

        /* --- Вкладки --- */
        button[data-baseweb="tab"] {
            font-size: 0.86rem !important;
            font-weight: 500 !important;
        }
        button[data-baseweb="tab"] p {color: var(--ink-muted) !important;}
        button[data-baseweb="tab"][aria-selected="true"] p {color: var(--brand) !important;}
        div[data-baseweb="tab-highlight"] {background: var(--brand) !important;}

        /* --- Уведомления: тонкая цветная полоса слева вместо заливки --- */
        div[data-testid="stAlert"] {
            border-radius: 5px;
            border: 1px solid var(--line);
            border-left: 3px solid var(--brand);
            background: var(--surface-alt);
            box-shadow: none;
        }
        div[data-testid="stAlert"] p {font-size: 0.86rem;}

        /* --- Радио и селект --- */
        div[role="radiogroup"] label p {font-size: 0.86rem !important;}
        div[data-baseweb="select"] > div {
            border-radius: 5px !important;
            border-color: var(--line) !important;
            font-size: 0.86rem;
        }

        hr, [data-testid="stDivider"] {border-color: var(--line) !important;}
        </style>
        """,
        unsafe_allow_html=True
    )


def section_label(text: str) -> None:
    """Надзаголовок раздела мелкими прописными — вместо эмодзи-иконок."""
    st.markdown(f'<div class="section-label">{text}</div>', unsafe_allow_html=True)


@st.cache_data(show_spinner="Сборка Baseline и проверка рисков...")
def process_baseline(file_bytes: bytes) -> dict[str, Any]:
    """Парсит исходный датасет и строит Baseline тем же конвейером, что и пересчёт.

    Задачи в работе планируются на остаток (смета − Spent_time_roles), ёмкость
    команды — средний velocity × 0.8 (backend.recalculation.pipeline.week0_from_excel).
    """
    state = week0_from_excel(file_bytes)
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


def _accept_fact(sprint: int, uploaded: Any, master: dict[str, Any], confirmed: bool = False) -> None:
    """Сохраняет файл спринта и пересчитывает его и все следующие загруженные спринты.

    Файл с другим номером спринта в имени не применяется без подтверждения.
    """
    name, data = (uploaded if isinstance(uploaded, tuple) else (uploaded.name, uploaded.getvalue()))
    if not confirmed and _file_sprint_mismatch(name, sprint):
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
        st.sidebar.warning(f"{_file_sprint_mismatch(name, sprint)} Файл «{name}» пока не применён.")
        col_yes, col_no = st.sidebar.columns(2)
        if col_yes.button("Всё равно загрузить", key="pending_yes", use_container_width=True):
            _accept_fact(sprint, (name, data), master, confirmed=True)
        if col_no.button("Отмена", key="pending_no", use_container_width=True):
            st.session_state.pop("pending_fact", None)
            st.rerun()

    for i in range(1, 7):
        if i in plans:
            name = files.get(i, ("файл загружен",))[0]
            with st.sidebar.expander(f"Спринт {i} · факт загружен", expanded=False):
                st.caption(name)
                col_replace, col_delete = st.columns(2)
                if col_replace.button("Заменить", key=f"replace_btn_{i}", use_container_width=True,
                                      help="Загрузить другой файл факта за этот спринт"):
                    st.session_state[f"replace_{i}"] = not st.session_state.get(f"replace_{i}", False)
                    st.rerun()
                if col_delete.button("Удалить", key=f"delete_btn_{i}", use_container_width=True,
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
    section_label("Квартальные показатели")
    st.caption("Знаменатель обеих метрик зафиксирован на Неделе 0 и не меняется при пересчёте.")

    if not baseline_tasks:
        st.info("Baseline пуст. Ожидание данных для расчета KPI.")
        return

    current_sprint = int(current_sprint or 0)
    metrics = kpi_breakdown(baseline_tasks, current_tasks, current_sprint=current_sprint)

    quarter_over = current_sprint >= 6
    pi_mode = "fact" if quarter_over else "forecast"
    pi_fact = metrics["pi_predictability"][pi_mode]["value"]
    pi_norm = [f"{v:g}" for v in metrics["norms"]["pi_predictability"]]
    pi_range = tuple(float(v) for v in metrics["norms"]["pi_predictability"])

    say_do_list = metrics["say_do"]
    latest_sd = next((row for row in say_do_list if row["sprint"] == current_sprint), None)
    sd_val = latest_sd["value"] if latest_sd else 0.0
    sd_norm = [f"{v:g}" for v in metrics["norms"]["say_do"]]
    sd_range = tuple(float(v) for v in metrics["norms"]["say_do"])

    col1, col2 = st.columns(2)

    with col1:
        pi_in_norm = pi_range[0] <= pi_fact <= pi_range[1]
        st.metric(
            label=(f"PI Predictability{'' if quarter_over else ' · прогноз'} "
                   f"(норма {pi_norm[0]}–{pi_norm[1]}%)"),
            value=f"{pi_fact:.1f}%",
            delta="В пределах нормы" if pi_in_norm else "Отклонение от нормы",
            delta_color="normal" if pi_in_norm else "inverse"
        )

    with col2:
        if latest_sd is None or latest_sd.get("status") in ("future", "no_data"):
            st.metric(
                label=f"Say/Do Ratio (норма {sd_norm[0]}–{sd_norm[1]}%)",
                value="—",
                delta=latest_sd["status_label"] if latest_sd else "Факт спринтов ещё не загружен",
                delta_color="off",
            )
            return
        sd_in_norm = sd_range[0] <= sd_val <= sd_range[1]
        st.metric(
            label=f"Say/Do Ratio · спринт {latest_sd['sprint']} (норма {sd_norm[0]}–{sd_norm[1]}%)",
            value=f"{sd_val:.1f}%",
            delta="В пределах нормы" if sd_in_norm else "Отклонение от нормы",
            delta_color="normal" if sd_in_norm else "inverse"
        )


def main() -> None:
    """Инициализирует Streamlit-страницу, State Machine и сайдбар-навигацию."""
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    inject_custom_css()
    init_session_state()

    if os.path.exists(LOGO_PATH):
        st.sidebar.image(LOGO_PATH, width="stretch")

    st.sidebar.markdown("### Данные и навигация")

    # СОСТОЯНИЕ 0: Мастер-датасет не загружен
    if "baseline" not in st.session_state.calculated_plans:
        st.title("Инициализация квартала")
        st.caption("Загрузите мастер-датасет, чтобы построить план квартала, риски и звёздную карту.")

        uploaded_file = st.file_uploader(
            "Загрузите Master-датасет (.xlsx)",
            type=["xlsx"],
            help="Файл инициализации плана. Служит основой для расчета Baseline и KPI."
        )

        if uploaded_file:
            try:
                base_state = process_baseline(uploaded_file.getvalue())
                st.session_state.master_dataset = base_state
                st.session_state.master_name = uploaded_file.name
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
        return  # Полная блокировка остального UI до загрузки Мастера

    # СОСТОЯНИЕ 1-6: Пайплайн загрузки спринтов в Сайдбаре
    st.sidebar.markdown("### Загрузка данных")

    with st.sidebar.expander("Мастер-датасет · Неделя 0", expanded=False):
        st.caption(st.session_state.get("master_name") or "Датасет загружен.")
        if st.button("Сбросить весь план и начать заново", use_container_width=True):
            st.session_state.calculated_plans = {}
            st.session_state.master_dataset = None
            st.session_state.fact_files = {}
            _reset_uploaders()
            st.rerun()

    render_fact_uploads(st.session_state.master_dataset)

    st.sidebar.markdown("---")

    # НАВИГАЦИЯ (Выбор текущего хронологического среза)
    st.sidebar.markdown("### Режим просмотра")

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

    page = st.sidebar.selectbox("Раздел", options=["План (PI-цикл)", "Всё сразу или по плану",
                                                   "Риски и Алерты", "Звёздная карта"])

    st.sidebar.markdown("---")
    st.sidebar.caption("Кронверкские псы · хакатон ПочтаТех")

    # РЕНДЕР ИНТЕРФЕЙСА
    active_state = st.session_state.calculated_plans[selected_key]
    plan_dict = active_state["plan"]
    alerts = active_state["alerts"]
    starmap = active_state["starmap"]

    render_kpi_widgets(plan_dict.get("baseline", []), plan_dict.get("current", []),
                       plan_dict.get("current_sprint", 0))
    st.divider()

    if page == "План (PI-цикл)":
        render_plan_view(plan_dict, selected_mode_label)
    elif page == "Всё сразу или по плану":
        render_overload_view((st.session_state.master_dataset or {}).get("overload"))
    elif page == "Риски и Алерты":
        render_risk_view(alerts, plan_dict.get("resolved_alerts") or [],
                         (st.session_state.master_dataset or {}).get("data_quality") or [])
    elif page == "Звёздная карта":
        render_star_map_view(starmap)


if __name__ == "__main__":
    main()
