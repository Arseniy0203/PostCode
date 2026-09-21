# Ответственный: Человек 3 (starmap + explainability)
"""Тесты контракта причин решений (backend/explainability/reasons.py)."""

from __future__ import annotations

import pytest

from backend.explainability.reasons import (
    ClosingResult,
    InclusionCode,
    ReasonCode,
    TargetOption,
    dependency_not_ready,
    explain_shift,
    included_fits,
    included_stretched,
    make_reason,
    no_resources,
    validate_decision,
)

# Значения справочников — дословно из датасета.
DATASET_REASONS = {
    "Не оценивалась": ReasonCode.NOT_ESTIMATED,
    "Отсутствие ресурсов": ReasonCode.NO_RESOURCES,
    "Отсутствует готовность смежных команд": ReasonCode.DEPENDENT_TEAM_NOT_READY,
    "Превышение плана": ReasonCode.PLAN_EXCEEDED,
    "Изменились требования": ReasonCode.REQUIREMENTS_CHANGED,
}
DATASET_TARGETS = {
    "1. Аналитика и архитектура выполнены частично": TargetOption.ANALYSIS_PARTIAL,
    "2. Аналитика и архитектура завершены": TargetOption.ANALYSIS_DONE,
    "3. Функционал реализован частично, работы будут продолжены": TargetOption.FUNC_PARTIAL,
    "4. Функционал реализован и готов к ИТ": TargetOption.READY_FOR_IT,
    "5. Функционал реализован и перенесен в опытную эксплуатацию": TargetOption.PILOT,
    "6. Функционал реализован и перенесен в промышленную эксплуатацию": TargetOption.PRODUCTION,
    "не будет взято в квартал": TargetOption.NOT_IN_QUARTER,
    "Отменено зака": TargetOption.CANCELLED_BY_CUSTOMER,  # обрезано в датасете
}
DATASET_RESULTS = {
    "планируемый результат работ достигнут": ClosingResult.ACHIEVED,
    "планируемый результат работ НЕ достигнут": ClosingResult.NOT_ACHIEVED,
    "Работы отменены/перенесены заказчиком": ClosingResult.CANCELLED_BY_CUSTOMER,
}


@pytest.mark.parametrize(
    "enum_cls,mapping",
    [(ReasonCode, DATASET_REASONS), (TargetOption, DATASET_TARGETS),
     (ClosingResult, DATASET_RESULTS)],
)
def test_from_label_matches_dataset(enum_cls, mapping) -> None:
    for text, expected in mapping.items():
        assert enum_cls.from_label(text) is expected
        assert enum_cls.from_label(f"  {text.upper()} ") is expected


def test_codes_keep_contract_names() -> None:
    assert ReasonCode.NO_RESOURCES == "NO_RESOURCES"
    assert ReasonCode("PLAN_EXCEEDED") is ReasonCode.PLAN_EXCEEDED


def test_to_dict_has_planned_task_fields() -> None:
    reason = no_resources("DB-202", "Разработчик .net", 130, 80, sprint=2)
    data = reason.to_dict()
    assert data["decision"] == "postponed"
    assert data["reason_code"] == "NO_RESOURCES"
    assert data["reason_label"] == "Отсутствие ресурсов"
    assert data["target_option"] == "NOT_IN_QUARTER"
    assert data["reason_facts"]["need_hh"] == 130
    assert "50 ч" in data["reason_detail"]


def test_empty_detail_rejected() -> None:
    with pytest.raises(ValueError):
        make_reason(ReasonCode.NO_RESOURCES, "   ")


def test_code_must_match_decision() -> None:
    with pytest.raises(ValueError):
        make_reason(ReasonCode.NO_RESOURCES, "текст", decision="included")
    with pytest.raises(ValueError):
        make_reason(InclusionCode.FITS_CAPACITY, "текст", decision="postponed")


def test_make_reason_accepts_russian_label() -> None:
    reason = make_reason("Превышение плана", "текст", decision="cancelled")
    assert reason.code is ReasonCode.PLAN_EXCEEDED
    assert reason.decision == "cancelled"


def test_included_requires_reason() -> None:
    with pytest.raises(ValueError):
        validate_decision("included", None)
    reason = included_fits("SRV-4011", "Team-Alpha", 1, 1, 8, 10.4)
    assert validate_decision("included", reason) is reason
    with pytest.raises(ValueError):
        validate_decision("postponed", reason)


def test_stretched_text_for_db202() -> None:
    reason = included_stretched("DB-202", "Разработчик .net", 130, 80, 2, 3)
    assert reason.code is InclusionCode.STRETCHED
    assert "2 спринта" in reason.detail


def test_dependency_reason_names_blocker() -> None:
    reason = dependency_not_ready("MP-102", "DB-202", "Team-Platform", 3)
    assert reason.code is ReasonCode.DEPENDENT_TEAM_NOT_READY
    assert "DB-202" in reason.detail and "Team-Platform" in reason.detail


def test_explain_shift_flags_quarter_risk() -> None:
    shift = explain_shift("DB-203", 5, 7, prodf_id="PRODF-7124", cause_task_id="DB-202")
    assert shift.delta == 2 and shift.quarter_at_risk
    assert "PRODF-7124" in shift.detail and "DB-202" in shift.detail

    ok = explain_shift("DB-203", 3, 4, prodf_id="PRODF-7124", cause_text="сдвиг факта")
    assert not ok.quarter_at_risk


def test_explain_shift_requires_cause() -> None:
    with pytest.raises(ValueError):
        explain_shift("DB-203", 3, 4, prodf_id="PRODF-7124")


def test_short_prefix_does_not_match_target() -> None:
    # «1» из ячейки Excel не должна превращаться в цель «1. Аналитика ...».
    with pytest.raises(ValueError):
        TargetOption.from_label("1")
    assert TargetOption.from_label("не будет взято") is TargetOption.NOT_IN_QUARTER


def test_no_resources_without_deficit() -> None:
    reason = no_resources("A1", "Разработчик Java", need_hh=10, available_hh=80, sprint=1)
    assert "-" not in reason.detail
    assert reason.facts["deficit_hh"] == 0.0


def test_shift_without_change_is_rejected() -> None:
    with pytest.raises(ValueError):
        explain_shift("A1", 3, 3, prodf_id="P1", cause_text="ничего не поменялось")
