"""Нормализация компетенций для звёздной карты.

Компетенция у нас двух уровней:
    role — роль из сметы задач («Разработчик .net»). Только через роль задача
        связывается с людьми: технологий в задачах нет, есть часы по ролям;
    tech — заявленная технология из skills_declared («C#», «Kubernetes»).

Без нормализации Bus Factor врёт: в датасете одна и та же роль записана как
«Девопс» и «ДевОпс», «Разработчик IOS» и «Разработчик iOS», а строка
«CI/CD (GitLab, Jenkins)» при наивном разбиении по запятой превращается
в две несуществующие технологии.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from backend.recalculation.plan_fields import normalize_role

# Разные написания одной технологии в профилях инженеров.
TECH_ALIASES: dict[str, str] = {
    "apachekafka": "Kafka",
    "corespring": "Spring Framework",
    "springcore": "Spring Framework",
    "springframework": "Spring Framework",
    "microservices": "Микросервисы",
    "microservicesархитектура": "Микросервисы",
    "микросервисы": "Микросервисы",
    "java": "Java",
    "core": "Java Core",
    "javacore": "Java Core",
    "ci/cd": "CI/CD",
    "gitlabci": "CI/CD",
}

# Роль -> ключевые технологии. Нужно для правила технологического соответствия
# и для понятия «критическая технология»: задачи знают только роли.
ROLE_CORE_TECH: dict[str, tuple[str, ...]] = {
    "разработчикjava": ("Java", "Spring Framework"),
    "разработчикjavascript": ("JavaScript",),
    "разработчик.net": ("C#", ".NET Core"),
    "разработчик1с": ("1С",),
    "разработчикandroid": ("Kotlin", "Android SDK"),
    "разработчикios": ("Swift", "UIKit"),
    "разработчикbigdata": ("Python", "PySpark", "Hadoop"),
    "аналитикbigdata": ("Python", "SQL"),
    "аналитик": ("SQL", "BPMN"),
    "архитекторрешений": ("Архитектура ПО", "Микросервисы"),
    "девопс": ("Docker", "Kubernetes", "CI/CD"),
    "дизайнер": ("Figma", "UX/UI"),
    "тестировщик": ("REST API", "SQL"),
    "тестировщик(автотестирование)": ("Selenium WebDriver", "Pytest"),
    "руководительbigdata": ("Управление Data-командами",),
}


# Роли пишутся в датасете по-разному: в профилях инженеров «Девопс» и
# «Разработчик IOS», в смете задач «ДевОпс» и «Разработчик iOS».
ROLE_DISPLAY: dict[str, str] = {
    "девопс": "ДевОпс",
    "разработчикios": "Разработчик iOS",
    "разработчикbigdata": "Разработчик Big Data",
}


def split_skills(raw: Any) -> list[str]:
    """Разбивает список навыков, не ломая скобки.

    «CI/CD (GitLab, Jenkins), Docker» -> ["CI/CD (GitLab, Jenkins)", "Docker"].
    Принимает и строку, и уже готовый список.
    """
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        parts: list[str] = []
        for item in raw:
            parts.extend(split_skills(item))
        return parts
    text = str(raw)
    parts, depth, current = [], 0, ""
    for char in text:
        if char in "([":
            depth += 1
        elif char in ")]":
            depth = max(depth - 1, 0)
        if char == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += char
    parts.append(current)
    return [p.strip() for p in parts if p.strip() and p.strip().lower() != "none"]


def tech_key(name: str) -> str:
    """Ключ технологии: регистр, пробелы и «ё» не различаются."""
    return "".join(str(name).split()).casefold().replace("ё", "е")


def canonical_tech(name: str) -> str:
    """Каноническое имя технологии (Apache Kafka и Kafka — одно и то же).

    Уточнение в скобках не делает технологию другой: «CI/CD (GitLab, Jenkins)»,
    «CI/CD» и «GitLab CI» — один навык CI/CD.
    """
    cleaned = " ".join(str(name).split())
    bare = re.sub(r"\s*\(.*?\)\s*", " ", cleaned).strip() or cleaned
    return TECH_ALIASES.get(tech_key(bare), TECH_ALIASES.get(tech_key(cleaned), bare))


def role_key(name: str) -> str:
    """Ключ роли — общий с планировщиком и пересчётом."""
    return normalize_role(str(name).replace("ё", "е"))


def core_tech_for_role(role: str) -> tuple[str, ...]:
    """Ключевые технологии роли (см. ROLE_CORE_TECH)."""
    return ROLE_CORE_TECH.get(role_key(role), ())


def role_display_names(engineers: Iterable[dict[str, Any]]) -> dict[str, str]:
    """Ключ роли -> самое частое человекочитаемое написание."""
    counts: dict[str, dict[str, int]] = {}
    for eng in engineers:
        role = eng.get("role")
        if not role:
            continue
        name = " ".join(str(role).split())
        counts.setdefault(role_key(name), {}).setdefault(name, 0)
        counts[role_key(name)][name] += 1
    return {
        key: ROLE_DISPLAY.get(key, max(names, key=names.get))
        for key, names in counts.items()
    }
