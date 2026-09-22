# ЭТОТ ФАЙЛ — КОНТРАКТ, ИСПОЛЬЗУЕТСЯ planning/scheduler.py И
# recalculation/recalc.py, risk/alerts.py.
"""Причины решений планировщика: включение, перенос, отмена и сдвиг задач.

По условию сервис обязан объяснять причины включения, переноса и отмены задач,
а при пересчёте — какие отклонения вызвали изменения и какие цели под угрозой.

Справочники (значения — дословно из датасета):
    ReasonCode     — «Причины расхождения планов заказчика и исполнителя»
                     (для postponed / cancelled);
    InclusionCode  — основания включения задачи в план (для included);
    TargetOption   — «Итог разработки, выбирается заказчиком и исполнителем»;
    ClosingResult  — «Результаты работ при закрытии задачи».

Вызывающая сторона не собирает тексты вручную, а пользуется шаблонами
(no_resources, dependency_not_ready, included_fits, ...): так все причины
единообразны и содержат цифры (facts), по которым принято решение.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

Decision = Literal["included", "postponed", "cancelled"]
DECISIONS: tuple[str, ...] = ("included", "postponed", "cancelled")

# Квартал = 6 двухнедельных спринтов (Условие задачи).
QUARTER_SPRINTS = 6


def _norm_label(text: str) -> str:
    return " ".join(str(text).split()).casefold().rstrip(".")


class _LabeledEnum(str, Enum):
    """Enum с машинным кодом (value) и русским текстом из справочника (label)."""

    label: str

    def __new__(cls, value: str, label: str) -> "_LabeledEnum":
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.label = label
        return obj

    def __str__(self) -> str:
        return self.value

    @classmethod
    def from_label(cls, text: str) -> "_LabeledEnum":
        """Находит элемент по русскому тексту из Excel или по коду.

        Регистр и лишние пробелы не важны. Допускается обрезанное значение
        (в датасете встречается «Отменено зака»), если префикс однозначен.
        """
        key = _norm_label(text)
        if not key:
            raise ValueError(f"{cls.__name__}: пустое значение")
        for item in cls:
            if key in (_norm_label(item.label), _norm_label(item.value)):
                return item
        # Префикс принимаем только достаточно длинный: иначе случайная "1"
        # из ячейки Excel совпадёт с «1. Аналитика и архитектура ...».
        if len(key) >= 5:
            prefixed = [item for item in cls if _norm_label(item.label).startswith(key)]
            if len(prefixed) == 1:
                return prefixed[0]
        raise ValueError(f"{cls.__name__}: неизвестное значение {text!r}")


class ReasonCode(_LabeledEnum):
    """Причины переноса/отмены — справочник «Причины расхождения планов»."""

    NOT_ESTIMATED = ("NOT_ESTIMATED", "Не оценивалась")
    NO_RESOURCES = ("NO_RESOURCES", "Отсутствие ресурсов")
    DEPENDENT_TEAM_NOT_READY = (
        "DEPENDENT_TEAM_NOT_READY",
        "Отсутствует готовность смежных команд",
    )
    PLAN_EXCEEDED = ("PLAN_EXCEEDED", "Превышение плана")
    REQUIREMENTS_CHANGED = ("REQUIREMENTS_CHANGED", "Изменились требования")


class InclusionCode(_LabeledEnum):
    """Основания включения задачи в план квартала."""

    HIGH_PRIORITY = ("HIGH_PRIORITY", "Высокий приоритет инициативы")
    FITS_CAPACITY = ("FITS_CAPACITY", "Укладывается в ресурс команды и ролей")
    UNBLOCKS_CHAIN = ("UNBLOCKS_CHAIN", "Блокирует другие задачи")
    ALREADY_IN_PROGRESS = ("ALREADY_IN_PROGRESS", "Задача уже в работе")
    STRETCHED = ("STRETCHED", "Растянута на несколько спринтов, укладывается в квартал")
    PARTIAL_TARGET = (
        "PARTIAL_TARGET",
        "Частичная цель: специалистов части ролей нет в компании",
    )


class TargetOption(_LabeledEnum):
    """Итог разработки — справочник «Варианты выбора цели»."""

    ANALYSIS_PARTIAL = ("ANALYSIS_PARTIAL", "1. Аналитика и архитектура выполнены частично")
    ANALYSIS_DONE = ("ANALYSIS_DONE", "2. Аналитика и архитектура завершены")
    FUNC_PARTIAL = (
        "FUNC_PARTIAL",
        "3. Функционал реализован частично, работы будут продолжены",
    )
    READY_FOR_IT = ("READY_FOR_IT", "4. Функционал реализован и готов к ИТ")
    PILOT = ("PILOT", "5. Функционал реализован и перенесен в опытную эксплуатацию")
    PRODUCTION = (
        "PRODUCTION",
        "6. Функционал реализован и перенесен в промышленную эксплуатацию",
    )
    NOT_IN_QUARTER = ("NOT_IN_QUARTER", "не будет взято в квартал")
    CANCELLED_BY_CUSTOMER = ("CANCELLED_BY_CUSTOMER", "Отменено заказчиком")


class ClosingResult(_LabeledEnum):
    """Справочник «Результаты работ при закрытии задачи»."""

    ACHIEVED = ("ACHIEVED", "планируемый результат работ достигнут")
    NOT_ACHIEVED = ("NOT_ACHIEVED", "планируемый результат работ НЕ достигнут")
    CANCELLED_BY_CUSTOMER = (
        "CANCELLED_BY_CUSTOMER",
        "Работы отменены/перенесены заказчиком",
    )


@dataclass(frozen=True)
class Reason:
    """Причина решения по задаче.

    decision — included / postponed / cancelled;
    code     — InclusionCode для included, ReasonCode для postponed/cancelled;
    detail   — человекочитаемый текст с цифрами;
    target   — итог разработки из справочника «Варианты выбора цели»;
    facts    — числа, на которых основано решение (для карточки задачи);
    recommendation — что можно сделать (ответ экспертов п. 4).
    """

    decision: Decision
    code: ReasonCode | InclusionCode
    detail: str
    target: TargetOption | None = None
    facts: dict[str, Any] = field(default_factory=dict)
    recommendation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Поля в формате PlannedTask (см. contracts/task_schema.md)."""
        return {
            "decision": self.decision,
            "reason_code": self.code.value,
            "reason_label": self.code.label,
            "reason_detail": self.detail,
            "target_option": self.target.value if self.target else None,
            "target_label": self.target.label if self.target else None,
            "reason_facts": dict(self.facts),
            "recommendation": self.recommendation,
        }


def _coerce_code(code: ReasonCode | InclusionCode | str) -> ReasonCode | InclusionCode:
    if isinstance(code, (ReasonCode, InclusionCode)):
        return code
    for enum_cls in (ReasonCode, InclusionCode):
        try:
            return enum_cls.from_label(code)
        except ValueError:
            pass
    raise ValueError(f"Неизвестный код причины: {code!r}")


def make_reason(
    code: ReasonCode | InclusionCode | str,
    detail_text: str,
    *,
    decision: Decision | None = None,
    target: TargetOption | str | None = None,
    facts: dict[str, Any] | None = None,
    recommendation: str | None = None,
) -> Reason:
    """Конструирует объект причины решения по задаче.

    Args:
        code: код из ReasonCode (перенос/отмена) или InclusionCode (включение);
            допускается строка-код или русский текст из справочника.
        detail_text: человекочитаемое пояснение причины, не пустое.
        decision: решение по задаче. Если не задано: InclusionCode -> "included",
            ReasonCode -> "postponed".
        target: итог разработки из справочника «Варианты выбора цели».
        facts: числа, на которых основано решение.

    Returns:
        Reason с указанными полями.

    Raises:
        ValueError: пустой detail, неизвестный код/решение, или код не
            соответствует решению (например, ReasonCode при "included").
    """
    code = _coerce_code(code)
    if decision is None:
        decision = "included" if isinstance(code, InclusionCode) else "postponed"
    if decision not in DECISIONS:
        raise ValueError(f"Неизвестное решение: {decision!r}")
    if decision == "included" and not isinstance(code, InclusionCode):
        raise ValueError(f"Для included нужен InclusionCode, получен {code!r}")
    if decision != "included" and not isinstance(code, ReasonCode):
        raise ValueError(f"Для {decision} нужен ReasonCode, получен {code!r}")
    if decision == "cancelled" and code is ReasonCode.NO_RESOURCES:
        # Ответ экспертов п. 4: нет ресурсов — задача переносится, а не отменяется.
        raise ValueError("Нехватка ресурсов — это перенос (postponed), а не отмена")
    if not detail_text or not detail_text.strip():
        raise ValueError("reason_detail не может быть пустым")
    if isinstance(target, str):
        target = TargetOption.from_label(target)
    if decision == "included" and target is None:
        # Включённая задача по умолчанию идёт до полной цели.
        target = TargetOption.PRODUCTION
    return Reason(decision, code, detail_text.strip(), target, dict(facts or {}),
                  (recommendation or "").strip() or None)


def validate_decision(decision: str, reason: Reason | None) -> Reason:
    """Проверяет, что решение сопровождается согласованной причиной.

    Причина обязательна для любого решения, включая "included".

    Raises:
        ValueError: причины нет или она не соответствует решению.
    """
    if decision not in DECISIONS:
        raise ValueError(f"Неизвестное решение: {decision!r}")
    if reason is None:
        raise ValueError(f"Решение {decision!r} без причины")
    if reason.decision != decision:
        raise ValueError(
            f"Причина относится к решению {reason.decision!r}, а не {decision!r}"
        )
    return reason


def _fmt(value: float) -> str:
    return f"{value:g}"


def _sprints_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "спринт"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "спринта"
    return "спринтов"


# --- Шаблоны: перенос / отмена -------------------------------------------------


def no_resources(
    task_id: str,
    role: str,
    need_hh: float,
    available_hh: float,
    sprint: int | None = None,
    *,
    decision: Decision = "postponed",
    target: TargetOption = TargetOption.NOT_IN_QUARTER,
) -> Reason:
    """Не хватает часов специалистов роли."""
    where = f" в спринте {sprint}" if sprint is not None else " до конца квартала"
    deficit = need_hh - available_hh
    if deficit > 0:
        detail = (
            f"Не хватает {_fmt(deficit)} ч роли «{role}»{where}: "
            f"нужно {_fmt(need_hh)} ч, доступно {_fmt(available_hh)} ч."
        )
    else:
        detail = (
            f"Свободные часы роли «{role}»{where} ({_fmt(available_hh)} ч при нужных "
            f"{_fmt(need_hh)} ч) приходятся на спринты, где задачу уже не успеть "
            f"закончить: раньше их заняли задачи с более высоким приоритетом."
        )
    facts = {"task_id": task_id, "role": role, "need_hh": need_hh,
             "available_hh": available_hh, "deficit_hh": max(deficit, 0.0),
             "sprint": sprint}
    return make_reason(ReasonCode.NO_RESOURCES, detail, decision=decision,
                       target=target, facts=facts)


def no_tech_owner(
    task_id: str,
    tech: str,
    role: str | None = None,
    *,
    decision: Decision = "postponed",
    target: TargetOption = TargetOption.NOT_IN_QUARTER,
) -> Reason:
    """В команде нет инженера с требуемой технологией (правило соответствия)."""
    for_role = f" для роли «{role}»" if role else ""
    detail = f"Нет доступного инженера с технологией «{tech}»{for_role}."
    facts = {"task_id": task_id, "tech": tech, "role": role}
    return make_reason(ReasonCode.NO_RESOURCES, detail, decision=decision,
                       target=target, facts=facts)


def dependency_not_ready(
    task_id: str,
    blocking_task_id: str,
    blocking_team: str | None = None,
    expected_sprint: int | None = None,
    *,
    decision: Decision = "postponed",
    target: TargetOption = TargetOption.NOT_IN_QUARTER,
) -> Reason:
    """Блокирующая задача (часто другой команды) не будет готова вовремя."""
    team = f" команды {blocking_team}" if blocking_team else ""
    if expected_sprint is None:
        when = "не завершается в этом квартале"
    else:
        when = f"завершается только в спринте {expected_sprint}"
    detail = (
        f"Блокирующая задача {blocking_task_id}{team} {when}; "
        f"начать {task_id} можно не раньше следующего спринта после неё."
    )
    facts = {"task_id": task_id, "blocking_task_id": blocking_task_id,
             "blocking_team": blocking_team, "expected_sprint": expected_sprint}
    return make_reason(ReasonCode.DEPENDENT_TEAM_NOT_READY, detail,
                       decision=decision, target=target, facts=facts)


def plan_exceeded(
    task_id: str,
    team_id: str,
    need_sp: float,
    capacity_sp: float,
    sprint: int | None = None,
    *,
    decision: Decision = "postponed",
    target: TargetOption = TargetOption.NOT_IN_QUARTER,
) -> Reason:
    """Не хватает ёмкости команды в Story Points."""
    where = f"в спринте {sprint}" if sprint is not None else "в квартале"
    detail = (
        f"Ёмкость команды {team_id} {where} исчерпана: с задачей нужно "
        f"{_fmt(need_sp)} SP, доступно {_fmt(capacity_sp)} SP "
        f"(средний velocity × 0.8)."
    )
    facts = {"task_id": task_id, "team_id": team_id, "need_sp": need_sp,
             "capacity_sp": capacity_sp, "sprint": sprint}
    return make_reason(ReasonCode.PLAN_EXCEEDED, detail, decision=decision,
                       target=target, facts=facts)


def beyond_quarter(
    task_id: str,
    forecast_sprint: int,
    quarter_sprints: int = QUARTER_SPRINTS,
    *,
    decision: Decision = "postponed",
    target: TargetOption = TargetOption.NOT_IN_QUARTER,
) -> Reason:
    """Прогноз завершения выходит за последний спринт квартала."""
    detail = (
        f"Прогноз завершения — спринт {forecast_sprint}, а квартал заканчивается "
        f"спринтом {quarter_sprints} (12 недель)."
    )
    facts = {"task_id": task_id, "forecast_sprint": forecast_sprint,
             "quarter_sprints": quarter_sprints}
    return make_reason(ReasonCode.PLAN_EXCEEDED, detail, decision=decision,
                       target=target, facts=facts)


def not_estimated(
    task_id: str,
    missing: str = "Story Points и трудозатраты по ролям",
    *,
    decision: Decision = "postponed",
    target: TargetOption = TargetOption.NOT_IN_QUARTER,
) -> Reason:
    """У задачи нет оценки — её нельзя разместить в плане."""
    detail = f"Задача {task_id} не оценена: отсутствуют {missing}."
    return make_reason(ReasonCode.NOT_ESTIMATED, detail, decision=decision,
                       target=target, facts={"task_id": task_id, "missing": missing})


def requirements_changed(
    task_id: str,
    note: str,
    *,
    decision: Decision = "cancelled",
    target: TargetOption = TargetOption.CANCELLED_BY_CUSTOMER,
) -> Reason:
    """Заказчик изменил требования."""
    detail = f"Изменились требования: {note}"
    return make_reason(ReasonCode.REQUIREMENTS_CHANGED, detail, decision=decision,
                       target=target, facts={"task_id": task_id, "note": note})


# --- Шаблоны: включение --------------------------------------------------------


def included_fits(
    task_id: str,
    team_id: str,
    start_sprint: int,
    end_sprint: int,
    need_sp: float,
    capacity_sp: float,
) -> Reason:
    """Задача укладывается в ёмкость команды и часы ролей."""
    span = (f"спринт {start_sprint}" if start_sprint == end_sprint
            else f"спринты {start_sprint}–{end_sprint}")
    detail = (
        f"Поставлена в {span}: команда {team_id} укладывается в ёмкость "
        f"({_fmt(need_sp)} из {_fmt(capacity_sp)} SP), специалисты нужных ролей доступны."
    )
    facts = {"task_id": task_id, "team_id": team_id, "start_sprint": start_sprint,
             "end_sprint": end_sprint, "need_sp": need_sp, "capacity_sp": capacity_sp}
    return make_reason(InclusionCode.FITS_CAPACITY, detail, facts=facts)


def included_priority(
    task_id: str,
    prodf_id: str,
    score: float,
    rank: int,
    total: int | None = None,
) -> Reason:
    """Задача включена раньше других из-за высокого скоринга."""
    of_total = f" из {total}" if total else ""
    detail = (
        f"Включена в приоритетном порядке: инициатива {prodf_id}, "
        f"скоринг {_fmt(round(score, 2))}, место {rank}{of_total}."
    )
    facts = {"task_id": task_id, "prodf_id": prodf_id, "score": score,
             "rank": rank, "total": total}
    return make_reason(InclusionCode.HIGH_PRIORITY, detail, facts=facts)


def included_unblocks(task_id: str, blocked_task_ids: list[str]) -> Reason:
    """Задача включена, потому что от неё зависят другие."""
    detail = (
        f"Включена первой: от неё зависят задачи {', '.join(blocked_task_ids)}."
    )
    facts = {"task_id": task_id, "blocked_task_ids": list(blocked_task_ids)}
    return make_reason(InclusionCode.UNBLOCKS_CHAIN, detail, facts=facts)


def included_in_progress(task_id: str) -> Reason:
    """Задача уже в работе — продолжаем её."""
    detail = f"Задача {task_id} уже в работе, план продолжает её выполнение."
    return make_reason(InclusionCode.ALREADY_IN_PROGRESS, detail,
                       facts={"task_id": task_id})


def included_stretched(
    task_id: str,
    role: str,
    need_hh: float,
    per_sprint_hh: float,
    start_sprint: int,
    end_sprint: int,
) -> Reason:
    """Задача растянута на несколько спринтов из-за лимита часов роли."""
    if end_sprint <= start_sprint:
        raise ValueError(
            f"{task_id}: растянутая задача должна занимать больше одного спринта "
            f"({start_sprint}–{end_sprint})"
        )
    sprints = end_sprint - start_sprint + 1
    detail = (
        f"Растянута на {sprints} {_sprints_word(sprints)} ({start_sprint}–{end_sprint}): "
        f"роли «{role}» нужно {_fmt(need_hh)} ч, а доступно {_fmt(per_sprint_hh)} ч "
        f"за спринт. Завершение укладывается в квартал."
    )
    facts = {"task_id": task_id, "role": role, "need_hh": need_hh,
             "per_sprint_hh": per_sprint_hh, "start_sprint": start_sprint,
             "end_sprint": end_sprint}
    return make_reason(InclusionCode.STRETCHED, detail, facts=facts)


# --- Сдвиги при пересчёте ------------------------------------------------------


@dataclass(frozen=True)
class ShiftReason:
    """Почему при пересчёте сдвинулся срок задачи и что под угрозой."""

    task_id: str
    prodf_id: str
    old_sprint: int
    new_sprint: int
    detail: str
    cause_task_id: str | None = None
    cause_text: str | None = None
    quarter_at_risk: bool = False

    @property
    def delta(self) -> int:
        return self.new_sprint - self.old_sprint

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "prodf_id": self.prodf_id,
            "old_sprint": self.old_sprint,
            "new_sprint": self.new_sprint,
            "delta": self.delta,
            "cause_task_id": self.cause_task_id,
            "cause_text": self.cause_text,
            "quarter_at_risk": self.quarter_at_risk,
            "detail": self.detail,
        }


def explain_shift(
    task_id: str,
    old_sprint: int,
    new_sprint: int,
    *,
    prodf_id: str,
    cause_task_id: str | None = None,
    cause_text: str | None = None,
    quarter_sprints: int = QUARTER_SPRINTS,
) -> ShiftReason:
    """Объясняет сдвиг срока задачи при пересчёте плана.

    Args:
        task_id: сдвинутая задача.
        old_sprint: спринт окончания до пересчёта.
        new_sprint: спринт окончания после пересчёта (прогноз).
        prodf_id: инициатива, к которой относится задача.
        cause_task_id: задача-источник отклонения (для каскадного сдвига).
        cause_text: описание отклонения своими словами (для прочих причин).
        quarter_sprints: последний спринт квартала.

    Raises:
        ValueError: не указана ни cause_task_id, ни cause_text.
    """
    if not cause_task_id and not (cause_text and cause_text.strip()):
        raise ValueError("Нужно указать cause_task_id или cause_text")
    if old_sprint is None:
        at_risk = new_sprint > quarter_sprints
        detail = (f"{task_id} вошла в план: срок — спринт {new_sprint}"
                  + (f" ({cause_text.strip()})." if cause_text else "."))
        return ShiftReason(task_id, prodf_id, new_sprint, new_sprint, detail,
                           cause_task_id, cause_text, at_risk)
    if new_sprint == old_sprint:
        raise ValueError(
            f"{task_id}: сдвига нет, спринт окончания не изменился ({old_sprint})"
        )
    delta = new_sprint - old_sprint
    sign = f"+{delta}" if delta > 0 else str(delta)
    parts = [f"Срок {task_id} сдвинулся со спринта {old_sprint} на {new_sprint} ({sign})"]
    causes = []
    if cause_task_id and delta < 0:
        causes.append(f"задача {cause_task_id}, от которой она зависит, закрыта раньше срока")
    elif cause_task_id:
        causes.append(f"задача {cause_task_id}, от которой она зависит, не завершена в срок")
    if cause_text:
        causes.append(cause_text.strip())
    parts.append(": " + "; ".join(causes) + ".")
    at_risk = new_sprint > quarter_sprints
    if at_risk:
        parts.append(
            f" Инициатива {prodf_id} под угрозой: прогноз выходит за спринт "
            f"{quarter_sprints} (12 недель)."
        )
    else:
        parts.append(f" Инициатива {prodf_id} пока укладывается в квартал.")
    return ShiftReason(task_id, prodf_id, old_sprint, new_sprint, "".join(parts),
                       cause_task_id, cause_text, at_risk)


# --- Шаблоны: роли без людей, частичная цель, циклы ----------------------------

# Роли, без которых функционал можно сделать, но нельзя довести до эксплуатации.
_ROLLOUT_PREFIXES = ("руководитель", "специалистподдержки")


def _role_key(role: str) -> str:
    return "".join(str(role).split()).casefold().replace("-", "").replace("_", "")


def partial_target(missing_roles: list[str]) -> TargetOption:
    """Какую цель из «Вариантов выбора цели» можно обещать без ролей missing_roles.

    Нет только руководителя/поддержки — функционал делаем, но передать его в
    эксплуатацию некому: «4. Функционал реализован и готов к ИТ». Нет
    специалистов, которые делают сам функционал (аналитик, разработчик,
    тестировщик) — часть функционала не будет сделана: «3. Функционал реализован
    частично, работы будут продолжены».
    """
    keys = [_role_key(r) for r in missing_roles]
    if keys and all(k.startswith(_ROLLOUT_PREFIXES) for k in keys):
        return TargetOption.READY_FOR_IT
    return TargetOption.FUNC_PARTIAL


def _roles_text(missing: dict[str, float]) -> str:
    return ", ".join(f"«{r}» {_fmt(round(h, 1))} ч" for r, h in
                     sorted(missing.items(), key=lambda x: -x[1]))


def _hire_text(missing: dict[str, float]) -> str:
    roles = ", ".join(f"«{r}»" for r in sorted(missing, key=lambda r: -missing[r]))
    return (f"Нанять или обучить специалиста: {roles} — тогда задачу можно довести "
            f"до цели «{TargetOption.PRODUCTION.label}».")


def included_partial(
    task_id: str,
    team_id: str,
    start_sprint: int,
    end_sprint: int,
    missing: dict[str, float],
    planned_hh: float,
    target: TargetOption | None = None,
    *,
    busy: dict[str, float] | None = None,
) -> Reason:
    """Задача включена с частичной целью.

    missing — роли, которых нет в компании; busy — роли, которые есть, но их
    свободных часов до конца квартала не хватает. Их часы вынесены в дефицит.
    """
    busy = dict(busy or {})
    target = target or partial_target([*missing, *busy])
    span = (f"спринт {start_sprint}" if start_sprint == end_sprint
            else f"спринты {start_sprint}–{end_sprint}")
    why = []
    if missing:
        why.append(f"в компании нет специалистов ролей {_roles_text(missing)}")
    if busy:
        why.append(f"свободных часов ролей {_roles_text(busy)} до конца квартала не хватает — "
                   f"они заняты задачами с более высоким приоритетом")
    detail = (
        f"Поставлена в {span} с частичной целью «{target.label}»: {'; '.join(why)}. "
        f"Работа остальных ролей ({_fmt(round(planned_hh, 1))} ч) выполняется в квартале, "
        f"недостающие часы вынесены в дефицит."
    )
    advice = []
    if missing:
        advice.append(_hire_text(missing))
    if busy:
        roles = ", ".join(f"«{r}»" for r in sorted(busy, key=lambda r: -busy[r]))
        advice.append(f"Для полной цели привлечь из других команд или нанять: {roles}.")
    facts = {"task_id": task_id, "team_id": team_id, "start_sprint": start_sprint,
             "end_sprint": end_sprint, "missing_roles": dict(missing),
             "busy_roles": busy, "planned_hh": planned_hh}
    return make_reason(InclusionCode.PARTIAL_TARGET, detail, target=target,
                       facts=facts, recommendation=" ".join(advice))


def role_absent(
    task_id: str,
    missing: dict[str, float],
    total_hh: float,
    *,
    strict: bool = False,
) -> Reason:
    """В компании нет специалистов ролей задачи — задача переносится."""
    share = sum(missing.values()) / total_hh if total_hh else 1.0
    if strict:
        why = "по строгому правилу задача без всех ролей не берётся в квартал"
    elif share >= 1:
        why = "выполнить из неё нечего"
    else:
        why = (f"это {share:.0%} трудозатрат задачи — частичная цель не даст "
               f"осмысленного результата")
    detail = f"В компании нет специалистов ролей {_roles_text(missing)}: {why}."
    facts = {"task_id": task_id, "missing_roles": dict(missing),
             "missing_share": round(share, 3)}
    return make_reason(ReasonCode.NO_RESOURCES, detail, facts=facts,
                       target=TargetOption.NOT_IN_QUARTER,
                       recommendation=_hire_text(missing))


def dependency_cycle(task_id: str, cycle: list[str]) -> Reason:
    """Задача в цикле зависимостей — начать её нельзя ни в каком порядке."""
    path = " → ".join(cycle)
    detail = (f"Задача в цикле зависимостей ({path}): каждая ждёт другую, начать "
              f"ни одну нельзя.")
    return make_reason(ReasonCode.DEPENDENT_TEAM_NOT_READY, detail,
                       target=TargetOption.NOT_IN_QUARTER,
                       facts={"task_id": task_id, "cycle": list(cycle)},
                       recommendation="Уточнить у команд, какая связь лишняя, и "
                                      "поправить Task_dependencies.")


def unknown_team(task_id: str, team_id: str) -> Reason:
    """Для команды нет истории velocity — ёмкость в SP неизвестна."""
    detail = (f"Для команды {team_id or '—'} нет истории velocity (Team_history), "
              f"поэтому её ёмкость в SP неизвестна и задачу нельзя разместить.")
    return make_reason(ReasonCode.NO_RESOURCES, detail,
                       target=TargetOption.NOT_IN_QUARTER,
                       facts={"task_id": task_id, "team_id": team_id},
                       recommendation="Добавить снапшоты velocity команды в Team_history.")
