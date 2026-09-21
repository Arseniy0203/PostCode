# Ответственный: Человек 2 (recalculation + risk)
"""Отчёт о качестве исходных данных.

Ответ экспертов: задачи-призраки и прочие ошибки таблиц не «чиним», а
валидируем и выносим наружу. Работает на сырых данных, до планирования.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable

from backend.recalculation.plan_fields import normalize_role, prodf_id, task_id
from backend.risk.alerts import Alert

TOTAL_ROW = normalize_role("Итого по ролям")


def _info(tid: str, prodf: str, description: str, recommendation: str,
          affected: Iterable[str] = ()) -> Alert:
    return Alert(
        type="info",
        category="data_quality",
        task_id=tid,
        prodf_id_at_risk=prodf,
        description=description,
        sprint_detected=0,
        recommendation=recommendation,
        affected_task_ids=tuple(affected) or (tid,),
    )


def check_data_quality(
    tasks: list[dict[str, Any]],
    estimates: list[dict[str, Any]],
    dependencies: list[dict[str, Any]],
) -> list[Alert]:
    """Задачи-призраки, дубли связей, расхождение estimated_hh с калькулятором.

    Args:
        tasks: задачи из Tasks.
        estimates: калькулятор сметы в длинном формате {task_id, role, hours}.
        dependencies: ВСЕ связи, включая отброшенные при парсинге.
    """
    by_id = {task_id(t): t for t in tasks}
    alerts: list[Alert] = []

    def prodf_of(*ids: str) -> str:
        return next((prodf_id(by_id[i]) for i in ids if i in by_id), "")

    for dep in dependencies:
        a, b = dep.get("blocking_task_id"), dep.get("blocked_task_id")
        ghosts = [x for x in (a, b) if x not in by_id]
        if ghosts:
            alerts.append(_info(
                a, prodf_of(a, b),
                f"Связь {a} → {b} ({dep.get('dependency_type', '—')}) ссылается на "
                + (f"задачу-призрак {ghosts[0]}: её нет" if len(ghosts) == 1
                   else f"задачи-призраки {', '.join(ghosts)}: их нет")
                + " в таблице Tasks. Связь исключена из планирования.",
                "Уточнить у владельца данных, существует ли задача, и поправить Task_dependencies.",
                affected=(a, b),
            ))

    pairs = Counter((d.get("blocking_task_id"), d.get("blocked_task_id")) for d in dependencies)
    for (a, b), count in pairs.items():
        if count > 1:
            types = sorted({d.get("dependency_type", "—") for d in dependencies
                            if (d.get("blocking_task_id"), d.get("blocked_task_id")) == (a, b)})
            alerts.append(_info(
                a, prodf_of(a, b),
                f"Связь {a} → {b} указана {count} раза с типами: {', '.join(types)}.",
                "Оставить одну связь; для планирования все типы равнозначны.",
                affected=(a, b),
            ))

    calc: dict[str, float] = defaultdict(float)
    for row in estimates:
        if normalize_role(row.get("role", "")) != TOTAL_ROW:
            calc[str(row.get("task_id"))] += float(row.get("hours") or 0)

    for tid in sorted(set(calc) - set(by_id)):
        alerts.append(_info(
            tid, "",
            f"В калькуляторе сметы есть задача {tid} ({calc[tid]:g} ч), которой нет в Tasks.",
            "Проверить, не потеряна ли задача при выгрузке; в планировании не участвует.",
        ))

    for tid, task in by_id.items():
        declared = task.get("estimated_hh")
        if not isinstance(declared, (int, float)) or tid not in calc:
            continue
        if abs(float(declared) - calc[tid]) > 0.01:
            alerts.append(_info(
                tid, prodf_id(task),
                f"estimated_hh в Tasks = {float(declared):g} ч, а сумма калькулятора сметы "
                f"по ролям = {calc[tid]:g} ч. Значение в Tasks некорректно.",
                f"Перезаполнить estimated_hh значением калькулятора ({calc[tid]:g} ч) — "
                f"калькулятор является целевой сметой.",
            ))
    return alerts
