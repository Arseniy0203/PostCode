# Схема Alert

Ответственный: Человек 2 (recalculation + risk). Код: `backend/risk/alerts.py::Alert`,
в UI отдаётся через `Alert.to_dict()`.

| Поле               | Тип                                        | Описание |
|--------------------|--------------------------------------------|----------|
| type               | Literal["red", "yellow", "orange", "info"] | Цвет/важность. red/yellow/orange — обязательные по условию, info — дополнительные |
| category           | str                                        | `critical` / `cascade` / `resource` / `overrun` / `data_quality` |
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
  оставшиеся спринты задачи). Отдельно — один алерт на команду, если в ней нет ни одного
  инженера нужных ролей и задачи из-за этого перенесены (рекомендация «нанять/обучить»).
- **info / overrun** — по роли потрачено больше, чем заложено в калькуляторе сметы.
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

## Где лежат алерты в плане

После `apply_sprint_fact`: `plan["alerts"]` — список `Alert` последнего пересчёта,
`plan["history"][i]["alerts"]` — алерты каждого спринта в виде dict,
`plan["history"][i]["changes"]` — что поменялось в плане и почему.
