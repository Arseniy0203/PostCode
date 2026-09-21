# Схема Alert

Ответственный: Человек 2 (recalculation + risk). Код: `backend/risk/alerts.py::Alert`,
в UI отдаётся через `Alert.to_dict()`.

| Поле               | Тип                                        | Описание |
|--------------------|--------------------------------------------|----------|
| type               | Literal["red", "yellow", "orange", "info"] | Цвет/важность. red/yellow/orange — обязательные по условию, info — дополнительные |
| category           | str                                        | `critical` (red) / `cascade` (yellow) / `resource`, `hiring`, `capacity` (orange) / `blocked`, `overrun`, `stale`, `kpi`, `single_point`, `data_quality` (info) |
| task_id            | str                                        | Задача, породившая алерт (для orange — самая «тяжёлая» задача роли) |
| prodf_id_at_risk   | str                                        | Инициатива под риском. Обязательно для red/yellow/orange; у data_quality может быть `""`, если обе задачи — призраки |
| description        | str                                        | Что случилось, с цифрами и датами спринтов |
| recommendation     | str                                        | Что можно сделать (ответ экспертов: к переносу добавлять рекомендацию) |
| sprint_detected    | int                                        | Спринт пересчёта, на котором обнаружен (0 — Неделя 0) |
| team_id            | str \| None                                | Команда |
| role               | str \| None                                | Роль — только для orange |
| deficit_hh         | float \| None                              | Дефицит часов — только для orange |
| affected_task_ids  | list[str]                                  | Все задействованные задачи (для yellow — вся цепочка) |
| affected_prodf_ids | list[str]                                  | Все задействованные инициативы |
| status             | str                                        | `new` — появился в этом спринте, `ongoing` — висит с `since_sprint` |
| since_sprint       | int \| None                                | С какого спринта алерт держится |

Решённые алерты (были в прошлом спринте, пропали в этом) лежат отдельно в
`plan["resolved_alerts"]` / `history[i]["resolved_alerts"]` со `status="resolved"` и `resolved_sprint`.

Сортировка (`collect_alerts`): red → yellow → orange → info, затем по инициативе.

## Правила генерации

- **red / critical** — прогноз окончания задачи (`forecast_end_sprint`) > 6-го спринта, **или**
  задача была `included` в baseline, а при пересчёте стала `postponed`. Задачи, отложенные
  сразу на Неделе 0, алертом не считаются — это решение планировщика, а не отклонение.
- **yellow / cascade** — задача сдвинулась позже, чем в прошлом снапшоте (или в baseline),
  и от неё зависят незавершённые задачи. Алерт ставится на исходную задачу цепочки, в
  `affected_task_ids` — вся цепочка. Все три типа связей трактуются как «A блокирует B».
- **orange / resource** — на следующий спринт потребность роли в команде больше фонда часов
  (`80 × capacity_rate` по инженерам роли в команде; потребность = остаток часов задачи /
  оставшиеся спринты задачи). В рекомендации — конкретные инженеры этой роли из других
  команд со свободными часами в этом спринте («ENG-414 из Team-K (свободно 40 ч)»).
- **orange / hiring** — один алерт на команду, если в ней нет ни одного инженера нужных ролей
  и задачи из-за этого перенесены. По каждой роли одно из трёх: «нанять — в компании нет ни
  одного»; «нанять — в компании N чел., но до конца квартала свободно только X ч из нужных Y ч»
  (с тем, кого можно привлечь частично); «привлечь ENG-… из Team-… (свободно Z ч)».
  Свободные часы считаются по всем оставшимся спринтам, а не по одному.
- **orange / capacity** — задача отложена, хотя все роли в команде есть: (а) её SP больше
  ёмкости команды за спринт — совет разбить на части или частичная цель; (б) часы ролей заняты
  более приоритетными задачами — какой роли и сколько не хватает, нанять или привлечь.
- **info / blocked** — задача отложена, потому что не входит в квартал её блокирующая задача;
  цепочка до корневой причины («ждёт MP-102 → DB-202, корень — …»). Инвариант: у каждой
  отложенной задачи есть хотя бы один алерт с объяснением.
- **info / overrun** — по роли потрачено больше, чем заложено в калькуляторе сметы.
- **info / stale** — задача InProgress начата > 8 недель до старта квартала и не движется,
  или по ней нет прироста часов 2 спринта подряд.
- **info / kpi** — прогноз PI Predictability < 80% (список не успевающих инициатив) или
  Say/Do только что прошедшего спринта вне 90–105%. Считается через `backend/kpi/metrics.py`.
- **info / single_point** — один алерт на инженера, который единственный в компании носитель
  роли, нужной задачам плана (Bus Factor роли = 1). Это риск плана по ролям; Bus Factor по
  навыкам (skills_declared) — звёздная карта, `backend/starmap/`.
- **info / data_quality** — `backend/risk/data_quality.py::check_data_quality`: задачи-призраки
  в зависимостях и в калькуляторе, дубли связей, `estimated_hh` в Tasks ≠ калькулятор.

## Пример

```json
{
  "type": "yellow",
  "category": "cascade",
  "task_id": "DB-202",
  "prodf_id_at_risk": "PRODF-7124",
  "description": "Срок DB-202 сдвинулся со спринта 2 на 3 (+1): задача не уложилась в плановый спринт. Сдвигается цепочка зависимых задач: DB-203, MP-102, MP-103.",
  "recommendation": "Поднять приоритет DB-202 в команде Team-Platform: она блокирует 3 задач(и) в 2 инициатив(ах).",
  "sprint_detected": 2,
  "team_id": "Team-Platform",
  "role": null,
  "deficit_hh": null,
  "affected_task_ids": ["DB-202", "DB-203", "MP-102", "MP-103"],
  "affected_prodf_ids": ["PRODF-7124", "PRODF-7123"]
}
```

## Для UI: одна функция

```python
from backend.recalculation.pipeline import run_quarter
report = run_quarter("key_task_delay", until_sprint=3)   # 0 — только Неделя 0
```

`report`: `alerts` (dict), `resolved_alerts`, `by_prodf` (сводка по инициативам: `worst`,
`headline`, `counts`, `causes`, `recommendations`), `history` (по спринтам: `fact`, `changes`,
`alerts`), `data_quality`, `hiring` (`options[].headline` — «Нанять в Team-X: … → +N задач»,
`ceiling`), `hiring_roadmap` (жадный план найма: `headline` — «1. Нанять «…» в Team-X → +N
задач»), `summary` (что взято в квартал в baseline и сейчас), `quarter_summary` (итоги по
задачам и инициативам: `done` / `on_plan` / `carry_over` / `not_in_quarter` / `cancelled`,
до 6-го спринта — прогноз), `kpi`, `scenarios` (название → описание), `plan`.
Сценарии: `on_track`, `key_task_delay`, `engineer_leave`, `vacation`, `early_finish`,
`hire_role`; `target=` — ручной выбор задачи/инженера/`{"team_id","role"}`.

Факт спринта (`apply_sprint_fact`): по задаче — `status` (`Done` / `InProgress` / `Cancelled`),
`spent_hh` (часы за спринт), `estimated_hh` (переоценка), `note`; на уровне спринта —
`tasks_added`, `engineers_unavailable`, `engineers_returned`, `engineers_hired`.
Ошибочный факт отменяется `rollback(plan, sprint)`. Факт из Excel:
`load_fact_excel(path)` → `apply_uploaded_fact(plan, data, fact)`.

## Где лежат алерты в плане

После `apply_sprint_fact`: `plan["alerts"]` — список `Alert` последнего пересчёта,
`plan["history"][i]["alerts"]` — алерты каждого спринта в виде dict,
`plan["history"][i]["changes"]` — что поменялось в плане и почему.
