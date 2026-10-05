"""Длинные перечисления задач в текстах алертов — в компактный вид.

Бэкенд собирает описание одной строкой и перечисляет все затронутые задачи:
«… вынесены в дефицит: AI-303, ANL-3042, ASUKD-5918, …» — на экране это
превращается в сплошную простыню. Здесь список сворачивается до первых
нескольких и «ещё N», а полный состав показывается отдельно.
"""

from __future__ import annotations

import re

# Идентификаторы задач датасета: ONK-2475, SRV-4011, ASUKD-5918, PRODF-7120
TASK_ID = r"[A-ZА-Я][A-ZА-Я0-9]*-\d+"
TASK_RUN = re.compile(rf"{TASK_ID}(?:,\s*{TASK_ID})+")
KEEP = 3


def collapse_task_lists(text: str, keep: int = KEEP) -> tuple[str, list[str]]:
    """Укорачивает перечисления задач.

    Returns:
        (текст с «и ещё N», полный список задач из всех свёрнутых перечислений).
    """
    collected: list[str] = []

    def shorten(match: re.Match[str]) -> str:
        items = [item.strip() for item in match.group(0).split(",")]
        if len(items) <= keep + 1:
            return match.group(0)
        collected.extend(items)
        return f"{', '.join(items[:keep])} и ещё {len(items) - keep}"

    return TASK_RUN.sub(shorten, text or ""), collected
