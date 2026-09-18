# Схема Task / PlannedTask

Ответственный: Человек 1 (ingestion + planning)

## Task

Базовая сущность задачи, как она приходит из датасета (после `parse_dataset.py`).

| Поле            | Тип                                   | Описание                                                              |
|-----------------|----------------------------------------|------------------------------------------------------------------------|
| task_id         | str                                    | Уникальный идентификатор задачи                                       |
| parent_id       | str \| None                            | Идентификатор родительской задачи/PRODF-цели                          |
| prodf_id        | str                                    | Идентификатор PRODF-цели, к которой относится задача                  |
| team_id         | str                                    | Идентификатор команды-исполнителя                                     |
| status          | str                                    | Todo / InProgress / Done (на момент среза)                            |
| rung            | str                                    | Уровень иерархии (эпик/фича/задача — согласно датасету)                |
| estimation_sp   | float                                  | Оценка в Story Points                                                 |
| estimated_hh    | dict[str, float]                       | Оценка в человеко-часах по ролям, напр. `{"backend": 40, "qa": 8}`     |

## PlannedTask (расширяет Task)

Задача, помещённая в план (baseline или current).

| Поле            | Тип                                   | Описание                                                              |
|-----------------|-----------------------------------------|--------------------------------------------------------------------|
| planned_start   | int                                     | Номер спринта начала (план)                                        |
| planned_end     | int                                     | Номер спринта окончания (план)                                     |
| actual_start    | int \| None                             | Номер спринта фактического начала                                   |
| actual_end      | int \| None                             | Номер спринта фактического окончания                                |
| sprint_number   | int                                     | Спринт, к которому относится текущий срез строки плана               |
| decision        | Literal["included", "postponed", "cancelled"] | Решение планировщика по задаче                              |
| reason_code     | ReasonCode \| InclusionCode             | Код причины решения (см. `backend/explainability/reasons.py`)       |
| reason_detail   | str                                     | Человекочитаемое пояснение причины (с цифрами)                       |
| target_option   | TargetOption \| None                    | Итог разработки из справочника «Варианты выбора цели»                |
| reason_facts    | dict                                    | Числа, на которых основано решение (нужно/доступно ч, SP, блокер…)   |

## Примечания

- `estimated_hh` — результат транспонирования листа "роль x задача" в `parse_dataset.py`.
- **Любое** решение (`included`, `postponed`, `cancelled`) обязано сопровождаться
  `reason_code` и непустым `reason_detail` — по условию объясняются причины
  включения, переноса и отмены. Для `included` код из `InclusionCode`,
  для `postponed`/`cancelled` — из `ReasonCode`.

## Как заполнять причины (`backend/explainability/reasons.py`)

Тексты не пишем вручную — используем шаблоны, каждый возвращает `Reason`:

```python
from backend.explainability import reasons as R

# перенос / отмена (ReasonCode)
r = R.no_resources("DB-202", "Разработчик .net", need_hh=130, available_hh=80, sprint=2)
r = R.plan_exceeded("SRV-4012", "Team-Alpha", need_sp=14, capacity_sp=10.8, sprint=3)
r = R.dependency_not_ready("MP-102", "DB-202", "Team-Platform", expected_sprint=4)
r = R.beyond_quarter("AI-303", forecast_sprint=8)
r = R.not_estimated("KP-0000")
r = R.requirements_changed("SRV-4093", "заказчик исключил интеграцию", decision="cancelled")
r = R.no_tech_owner("AI-302", "Python")

# включение (InclusionCode)
r = R.included_fits("SRV-4011", "Team-Alpha", 1, 1, need_sp=8, capacity_sp=10.8)
r = R.included_priority("MP-101", "PRODF-7123", score=0.92, rank=1, total=45)
r = R.included_unblocks("DB-201", ["DB-202"])
r = R.included_in_progress("KP-3898")
r = R.included_stretched("DB-202", "Разработчик .net", 130, 80, 2, 3)

task.update(r.to_dict())   # decision, reason_code, reason_detail, target_option, reason_facts

# произвольная причина
r = R.make_reason(R.ReasonCode.PLAN_EXCEEDED, "текст", decision="cancelled")

# проверка перед сохранением плана
R.validate_decision(task["decision"], r)
```

Сдвиг срока при пересчёте (recalc.py / alerts.py):

```python
s = R.explain_shift("DB-203", old_sprint=4, new_sprint=7,
                    prodf_id="PRODF-7124", cause_task_id="DB-202")
s.detail            # «Срок DB-203 сдвинулся со спринта 4 на 7 (+3): задача DB-202 ...»
s.quarter_at_risk   # True — прогноз за пределами 6-го спринта
```

Строковые значения из Excel переводятся в коды через `from_label`:
`R.ReasonCode.from_label("Отсутствие ресурсов")`, `R.TargetOption.from_label("не будет взято в квартал")`.
