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
| status          | str                                    | ToDo / InProgress / Done (так в датасете; синонимы распознаются)      |
| rung            | int                                    | Ранг инициативы 65–95: чем выше, тем раньше задача в очереди          |
| estimation_sp   | float                                  | Оценка в Story Points                                                 |
| estimated_hh    | dict[str, float]                       | Часы по ролям из калькулятора сметы (целевая смета), напр. `{"Аналитик": 40}` |
| spent_hh        | dict[str, float]                       | Факт по ролям (Spent_time_roles + факты спринтов)                     |

## PlannedTask (расширяет Task)

Задача, помещённая в план (baseline или current).

| Поле            | Тип                                   | Описание                                                              |
|-----------------|-----------------------------------------|--------------------------------------------------------------------|
| planned_start_sprint | int \| None                       | Номер спринта начала (поля без `_sprint` в датасете — старые даты)   |
| planned_end_sprint   | int \| None                       | Номер спринта окончания по плану                                    |
| forecast_end_sprint  | int \| None                       | Прогноз окончания после пересчёта                                   |
| actual_end_sprint    | int \| None                       | Спринт, в котором задача закрыта (факт)                             |
| allocation      | dict[int, dict[str, float]]             | Спринт → {инженер: часы}                                            |
| assigned_engineers | list[str]                            | Инженеры задачи                                                     |
| sp_by_sprint    | dict[int, float]                        | SP, списанные в каждый спринт (пропорционально часам)                |
| unstaffed_hh    | dict[str, float]                        | Часы ролей, вынесенные в дефицит: роли нет в компании или её часы заняты (частичная цель) |
| target_label    | str \| None                             | Текст цели из справочника                                           |
| reason_label    | str                                     | Текст причины из справочника                                        |
| recommendation  | str \| None                             | Что можно сделать (ответ экспертов п. 4)                            |
| score           | float                                   | Скоринг: rung × (1 + 0.5 × число блокируемых задач)                 |
| sprint_number   | int                                     | Спринт, к которому относится текущий срез строки плана               |
| decision        | Literal["included", "postponed", "cancelled"] | Решение планировщика по задаче                              |
| reason_code     | ReasonCode \| InclusionCode             | Код причины решения (см. `backend/explainability/reasons.py`)       |
| reason_detail   | str                                     | Человекочитаемое пояснение причины (с цифрами)                       |
| target_option   | TargetOption                            | Итог разработки из справочника «Варианты выбора цели»: у включённой — `PRODUCTION` или частичная цель (`READY_FOR_IT`, `FUNC_PARTIAL`), у перенесённой — `NOT_IN_QUARTER` |
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

## Частичная цель (роли без людей)

`R.partial_target(missing_roles)`: нет только руководителя/поддержки → `READY_FOR_IT`
(«4. Функционал реализован и готов к ИТ»); иначе → `FUNC_PARTIAL`
(«3. Функционал реализован частично»). Шаблоны:

```python
r = R.included_partial("SRV-4011", "Team-Alpha", 1, 1, {"Руководитель проекта": 20}, 115,
                       busy={"ДевОпс": 10})        # busy — роль есть, но её часы заняты
r = R.role_absent("ANL-3042", {"Разработчик 1С": 50}, total_hh=90)   # > 50% сметы — перенос
r = R.dependency_cycle("C1", ["C1", "C2", "C1"])
r = R.unknown_team("T1", "Team-Z")
```

`make_reason` запрещает `NO_RESOURCES` для `cancelled` (нет ресурсов — это перенос) и
ставит `PRODUCTION` включённой задаче без явной цели.
