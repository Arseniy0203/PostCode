"""Готовый результат работы планировщика: Excel и PDF.

Колонки `плановый результат`, `будет включено в спринт` и `исполнитель` в
датасете заполнены только у Done — их и должен заполнить планировщик. Выгрузка
возвращает тот же состав задач с заполненными колонками, плюс листы по командам,
алерты и план найма: файл можно нести на PI-планирование.
"""

from __future__ import annotations

import io
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from backend.recalculation.plan_fields import (
    QUARTER_SPRINTS,
    end_sprint,
    field,
    prodf_id,
    remaining_hh,
    start_sprint,
    task_id,
    team_id,
)

FONT_DIR = Path(__file__).with_name("fonts")

DECISION_LABELS = {
    "included": "Включено в квартал",
    "postponed": "Перенесено",
    "cancelled": "Отменено",
}
ALERT_LABELS = {
    "red": "Критический",
    "yellow": "Каскадный сдвиг",
    "orange": "Ресурсный",
    "info": "Наблюдение",
}
NOT_IN_QUARTER = "не будет взято в квартал"

# Лист «План»: слева колонки датасета, дальше — то, что посчитал планировщик
PLAN_COLUMNS = [
    "Номер инициативы", "task_id", "summary", "team_id", "status",
    "плановый результат", "будет включено в спринт", "исполнитель",
    "Решение", "Причина", "Что сделать", "estimation_sp", "Часы сметы",
    "Часы без исполнителей", "Ждёт задачи", "Блокирует",
]


def _sprint_text(task: Any) -> str:
    """Чем заполняем колонку «будет включено в спринт»."""
    if field(task, "decision") in ("postponed", "cancelled"):
        return NOT_IN_QUARTER
    first, last = start_sprint(task), end_sprint(task)
    if not first:
        return ""
    if not last or last == first:
        return f"Спринт {first}"
    return f"Спринты {first}–{last}"


def _engineers(task: Any) -> str:
    """Исполнители: кого планировщик поставил на задачу, по спринтам."""
    allocation = field(task, "allocation") or {}
    if isinstance(allocation, dict) and allocation:
        names: dict[str, float] = defaultdict(float)
        for engineers in allocation.values():
            if isinstance(engineers, dict):
                for engineer, hours in engineers.items():
                    names[engineer] += float(hours or 0)
        if names:
            return ", ".join(f"{eng} ({round(hours)} ч)" for eng, hours
                             in sorted(names.items(), key=lambda kv: -kv[1]))
    assigned = field(task, "assigned_engineers") or []
    if assigned:
        return ", ".join(assigned) if isinstance(assigned, list) else str(assigned)
    # Людей нет в раскладке, когда работа по смете уже закрыта фактом
    if field(task, "decision") == "included" and not remaining_hh(task):
        return "работа закрыта по факту"
    return ""


def _hours(value: Any) -> float:
    if isinstance(value, dict):
        return round(sum(float(v or 0) for v in value.values()), 1)
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return 0.0


def _joined(value: Any) -> str:
    return ", ".join(value) if isinstance(value, (list, tuple)) else str(value or "")


def plan_rows(plan: dict[str, Any], team: str | None = None,
              prodf: str | None = None) -> list[dict[str, Any]]:
    """Строки листа «План». team/prodf — те же фильтры, что на странице плана."""
    rows = []
    for task in plan.get("current") or []:
        if team and team_id(task) != team:
            continue
        if prodf and prodf_id(task) != prodf:
            continue
        decision = field(task, "decision")
        rows.append({
            "Номер инициативы": prodf_id(task) or "",
            "task_id": task_id(task),
            "summary": field(task, "summary") or "",
            "team_id": team_id(task) or "",
            "status": field(task, "status") or "",
            "плановый результат": field(task, "target_label") or "",
            "будет включено в спринт": _sprint_text(task),
            "исполнитель": _engineers(task),
            "Решение": DECISION_LABELS.get(decision, "Закрыта до квартала"
                                           if field(task, "status") == "Done" else ""),
            "Причина": field(task, "reason_detail") or field(task, "reason_label") or "",
            "Что сделать": field(task, "recommendation") or "",
            "estimation_sp": field(task, "estimation_sp") or "",
            "Часы сметы": _hours(field(task, "estimated_hh")),
            "Часы без исполнителей": _hours(field(task, "unstaffed_hh")),
            "Ждёт задачи": _joined(field(task, "blocked_by")),
            "Блокирует": _joined(field(task, "blocks")),
        })
    rows.sort(key=lambda r: (r["team_id"], str(r["будет включено в спринт"]), r["task_id"]))
    return rows


def fact_template_rows(plan: dict[str, Any], engineers: Iterable[dict[str, Any]],
                       sprint: int) -> list[dict[str, Any]]:
    """Шаблон факта спринта: что по плану идёт в этом спринте и сколько часов.

    Команда правит статус и часы и загружает файл обратно — не нужно собирать
    таблицу с нуля и гадать про формат.
    """
    role_of = {str(e.get("engineer_id")): str(e.get("role") or "") for e in engineers or []}
    rows = []
    for task in plan.get("current") or []:
        if field(task, "status") == "Done" or field(task, "decision") != "included":
            continue
        allocation = field(task, "allocation") or {}
        hours = allocation.get(str(sprint)) or allocation.get(sprint) or {}
        closes = field(task, "forecast_end_sprint") == sprint or end_sprint(task) == sprint
        if not hours and not closes:
            continue
        row: dict[str, Any] = {
            "task_id": task_id(task),
            "summary": field(task, "summary") or "",
            "status": "Done" if closes else "InProgress",
        }
        for engineer, value in (hours or {}).items():
            role = role_of.get(str(engineer)) or str(engineer)
            row[role] = round(float(row.get(role, 0)) + float(value), 1)
        rows.append(row)
    return rows


def fact_template_bytes(plan: dict[str, Any], engineers: Iterable[dict[str, Any]],
                        sprint: int) -> bytes:
    """Книга Excel с шаблоном факта: лист «Факт спринта» и лист «Как заполнять»."""
    rows = fact_template_rows(plan, engineers, sprint)
    frame = pd.DataFrame(rows)
    howto = pd.DataFrame({f"Как заполнять факт спринта {sprint}": [
        "Колонка status: Done — задача закрыта, InProgress — работа идёт, "
        "Cancelled — отменена заказчиком.",
        "Колонки ролей: часы, фактически потраченные В ЭТОМ спринте (не нарастающим итогом).",
        "Задачу, которой не было в плане, можно дописать строкой — укажите task_id и часы.",
        "Строку задачи, по которой в спринте не работали, оставьте с нулями или удалите.",
        "Колонка summary — для чтения, система её не использует.",
    ]})
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        (frame if not frame.empty
         else pd.DataFrame(columns=["task_id", "summary", "status"])).to_excel(
            writer, sheet_name="Факт спринта", index=False)
        howto.to_excel(writer, sheet_name="Как заполнять", index=False)
        _autosize(writer)
    return buffer.getvalue()


def alert_rows(alerts: Iterable[Any] | None) -> list[dict[str, Any]]:
    rows = []
    for alert in alerts or []:
        data = alert if isinstance(alert, dict) else getattr(alert, "to_dict", dict)()
        rows.append({
            "Тип": ALERT_LABELS.get(data.get("type"), data.get("type", "")),
            "Что": data.get("category", ""),
            "Задача": data.get("task_id") or "",
            "Инициатива": data.get("prodf_id_at_risk") or "",
            "Команда": data.get("team_id") or "",
            "Роль": data.get("role") or "",
            "Описание": data.get("description", ""),
            "Что сделать": data.get("recommendation", ""),
        })
    return rows


def hiring_rows(starmap: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Кого нанять и кого обучить — из рекомендаций звёздной карты."""
    recommendations = (starmap or {}).get("recommendations") or {}
    rows = []
    for item in recommendations.get("hire") or []:
        candidate = (item.get("train_instead") or [None])[0]
        rows.append({
            "Действие": "Нанять",
            "Роль": item.get("role", ""),
            "Часов в плане": item.get("hours", 0),
            "Задач": len(item.get("tasks") or []),
            "Кандидат на обучение": (f"{candidate['engineer_id']} ({candidate.get('why', '')})"
                                     if candidate else ""),
        })
    for item in recommendations.get("train") or []:
        candidate = item.get("candidate") or {}
        rows.append({
            "Действие": "Обучить",
            "Роль": item.get("role", ""),
            "Часов в плане": item.get("hours", 0),
            "Задач": len(item.get("tasks") or []),
            "Кандидат на обучение": (f"{candidate.get('engineer_id', '')} "
                                     f"({candidate.get('why', '')})"),
        })
    return rows


def summary_lines(plan: dict[str, Any], rows: list[dict[str, Any]]) -> list[str]:
    """Короткая сводка в шапку обеих выгрузок."""
    decided = [r for r in rows if r["Решение"] in DECISION_LABELS.values()]
    included = [r for r in decided if r["Решение"] == DECISION_LABELS["included"]]
    postponed = [r for r in decided if r["Решение"] == DECISION_LABELS["postponed"]]
    cancelled = [r for r in decided if r["Решение"] == DECISION_LABELS["cancelled"]]
    sprint = plan.get("current_sprint") or 0
    lines = [
        f"Срез: {'Неделя 0' if not sprint else f'после спринта {sprint}'}, "
        f"квартал — {QUARTER_SPRINTS} спринтов по 2 недели",
        f"В квартале: {len(included)} из {len(decided)} задач; перенесено {len(postponed)}"
        + (f", отменено {len(cancelled)}" if cancelled else ""),
    ]
    initiatives = {r["Номер инициативы"] for r in included if r["Номер инициативы"]}
    if initiatives:
        lines.append(f"Инициатив в плане: {len(initiatives)}")
    return lines


def _teams(rows: list[dict[str, Any]]) -> list[str]:
    return sorted({r["team_id"] for r in rows if r["team_id"]})


def excel_bytes(plan: dict[str, Any], alerts: Iterable[Any] | None = None,
                starmap: dict[str, Any] | None = None, team: str | None = None,
                prodf: str | None = None) -> bytes:
    """Книга Excel: сводка, план, лист на каждую команду, алерты, найм."""
    rows = plan_rows(plan, team=team, prodf=prodf)
    plan_df = pd.DataFrame(rows, columns=PLAN_COLUMNS)
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame({"Квартальный план": summary_lines(plan, rows)}).to_excel(
            writer, sheet_name="Сводка", index=False)
        plan_df.to_excel(writer, sheet_name="План", index=False)
        for name in _teams(rows):
            team_df = plan_df[plan_df["team_id"] == name]
            # Имя листа в Excel — не длиннее 31 символа и без служебных знаков
            team_df.to_excel(writer, sheet_name=name[:31].replace("/", "-"), index=False)
        alerts_df = pd.DataFrame(alert_rows(alerts))
        if not alerts_df.empty:
            if team:
                alerts_df = alerts_df[(alerts_df["Команда"] == team) | (alerts_df["Команда"] == "")]
            alerts_df.to_excel(writer, sheet_name="Алерты", index=False)
        hiring_df = pd.DataFrame(hiring_rows(starmap))
        if not hiring_df.empty:
            hiring_df.to_excel(writer, sheet_name="Нанять и обучить", index=False)
        _autosize(writer)
    return buffer.getvalue()


def _autosize(writer: Any, limit: int = 60) -> None:
    """Ширина колонок по содержимому: иначе всё слипается в один столбец."""
    for sheet in writer.book.worksheets:
        for column in sheet.columns:
            width = max((len(str(cell.value)) for cell in column if cell.value), default=10)
            sheet.column_dimensions[column[0].column_letter].width = min(width + 2, limit)
        sheet.freeze_panes = "A2"


def pdf_bytes(plan: dict[str, Any], alerts: Iterable[Any] | None = None,
              starmap: dict[str, Any] | None = None, team: str | None = None,
              prodf: str | None = None) -> bytes:
    """Печатный отчёт: сводка, затем страница на каждую команду, затем риски."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                    TableStyle)

    # Встроенные шрифты reportlab без кириллицы — поэтому DejaVu лежит рядом
    for name, file in (("DejaVu", "DejaVuSans.ttf"), ("DejaVu-Bold", "DejaVuSans-Bold.ttf")):
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(FONT_DIR / file)))

    rows = plan_rows(plan, team=team, prodf=prodf)
    base = getSampleStyleSheet()["BodyText"]
    body = ParagraphStyle("body", parent=base, fontName="DejaVu", fontSize=7.5, leading=9.5)
    head = ParagraphStyle("head", parent=body, fontName="DejaVu-Bold", fontSize=7.5)
    title = ParagraphStyle("title", parent=base, fontName="DejaVu-Bold", fontSize=15, leading=19)
    subtitle = ParagraphStyle("subtitle", parent=body, fontSize=9, leading=13)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), title="Квартальный план",
                            leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=12 * mm, bottomMargin=12 * mm)
    story: list[Any] = [Paragraph("Квартальный план и риски", title), Spacer(1, 4 * mm)]
    for line in summary_lines(plan, rows):
        story.append(Paragraph(line, subtitle))

    columns = ["task_id", "summary", "будет включено в спринт", "estimation_sp",
               "плановый результат", "исполнитель", "Решение"]
    # В PDF читает человек, поэтому шапка словами, а не именами колонок датасета
    headers = ["Задача", "Название", "Спринты", "SP", "Цель в квартале", "Исполнители", "Решение"]
    widths = [22 * mm, 62 * mm, 24 * mm, 12 * mm, 58 * mm, 62 * mm, 28 * mm]
    grid = TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "DejaVu"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E7EDFA")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#294DA6")),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#D7DBE3")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7F8FA")]),
    ])

    for name in _teams(rows):
        team_rows = [r for r in rows if r["team_id"] == name]
        story.append(PageBreak())
        story.append(Paragraph(f"{name} — {len(team_rows)} задач", title))
        story.append(Spacer(1, 3 * mm))
        table = [[Paragraph(c, head) for c in headers]]
        table += [[Paragraph(str(row[c]), body) for c in columns] for row in team_rows]
        story.append(Table(table, colWidths=widths, repeatRows=1, style=grid))

    alert_table = alert_rows(alerts)
    if team:
        alert_table = [a for a in alert_table if a["Команда"] in (team, "")]
    if alert_table:
        story.append(PageBreak())
        story.append(Paragraph("Риски и алерты", title))
        story.append(Spacer(1, 3 * mm))
        alert_columns = ["Тип", "Задача", "Инициатива", "Роль", "Описание", "Что сделать"]
        alert_widths = [26 * mm, 22 * mm, 24 * mm, 34 * mm, 85 * mm, 77 * mm]
        table = [[Paragraph(c, head) for c in alert_columns]]
        table += [[Paragraph(str(a[c]), body) for c in alert_columns] for a in alert_table]
        story.append(Table(table, colWidths=alert_widths, repeatRows=1, style=grid))

    hiring = hiring_rows(starmap)
    if hiring:
        story.append(PageBreak())
        story.append(Paragraph("Кого нанять и кого обучить", title))
        story.append(Spacer(1, 3 * mm))
        hiring_columns = list(hiring[0])
        table = [[Paragraph(c, head) for c in hiring_columns]]
        table += [[Paragraph(str(h[c]), body) for c in hiring_columns] for h in hiring]
        story.append(Table(table, colWidths=[26 * mm, 52 * mm, 26 * mm, 20 * mm, 120 * mm],
                           repeatRows=1, style=grid))

    doc.build(story)
    return buffer.getvalue()
