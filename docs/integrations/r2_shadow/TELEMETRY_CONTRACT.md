# R2-SHADOW — разрешённая граница read-only телеметрии v1

Этот документ и приложенные schema/example — спецификация интерфейса, которую можно хранить в TORG_BOT. Они не содержат приватных исходников, внутренних конфигураций или raw CryptoLab. Подключение приёмника к работающей панели здесь не выполнено. Существующая v3.2/8083 не изменяется.

## Транспорт и полномочия

Производитель атомарно заменяет UTF-8 `telemetry.json`. Потребитель получает только чтение этого файла, проверяет `telemetry.schema.json`, `schema_version`, порядок/уникальность трёх профилей и возраст `generated_at`. Poll не чаще15 секунд. Не использовать `generated_at` как время оценки цены: для этого есть `valuation_at`. Будущее время или возраст producer snapshot более45 секунд — `STALE`; незнакомая версия/лишние поля/неполный JSON — `INVALID`. Не переносить предыдущий свежий статус на неверный snapshot.

При необходимости существующая общая панель может отдавать эти поля своим GET endpoint после отдельного тестирования. Спецификация не разрешает POST, команды, запуск/остановку, сброс капитала, доступ к SQLite/raw/code и передачу секретов. У R2 нет HTTP сервера или назначенного порта. Публикация схемы не означает подключение/развёртывание.

## Состав

- `schema_version, system, generated_at`: версия и время формирования.
- `deployment_status`: NOT_DEPLOYED либо STARTED_UNVERIFIED; локальный PASS не превращается в DEPLOYED.
- `process_status, heartbeat_at`: ARMED/OBSERVING только при heartbeat не старше45 секунд, иначе NO_FRESH_HEARTBEAT.
- `cohort`: опубликованные T0/minimum_end и индикатор наличия start receipt. Значение true не доказывает работоспособность VPS; сопоставлять с heartbeat.
- `market`: BYBIT_SPOT_LONG_CASH_NO_LEVERAGE. Не смешивать с фьючерсами, funding и плечом других систем.
- `profiles`: ровно три записи в порядке R2:300:BASE:OBSERVED_BOOK, R2:300:STRESS:OBSERVED_BOOK, BASELINE:300:BASE:OBSERVED_BOOK.
- В каждом профиле: initial capital, cash/quantities, Equity/Net PnL/return, MDD/risk, liquidation/dust, exposure/cash fraction, timestamp/status оценки; стоимость/оборот/число оценённых и отклонённых legs; последние512 точек equity с собственными timestamp.
- `comparison`: R2 BASE minus T01 BASE, USDT и процентные пункты, только при общей оценочной операции и свежих данных обоих профилей.
- `signal, target_weights, last_signal_at`: публичные scores, eligibility и веса в BTC/ETH/SOL-порядке; это последнее принятое решение, а не текущая пересчитанная рекомендация.
- `missed_decisions, missed_executions, data_errors`: счётчики пропусков/ошибок без приватных текстов исключений.
- `real_orders=0, demo_orders=0`: capability/runtime assertion бумажного контура, а не аудит внешнего биржевого аккаунта.
- `economic_verdict, fill_claim`: неизменные ограничения интерпретации.

## Семантика чисел

Все денежные показатели — USDT. `net_return_pct` измеряется в процентах, `r2_minus_t01_pp` — в процентных пунктах. `mdd_pct` — signed drawdown от peak, значение <=0. Последний исторический MDD/risk остаётся доступным при неизвестной текущей оценке.

Свежесть valuation — максимум120 секунд; при неизвестной/устаревшей цене Equity, PnL, return, exposure, liquidation/dust — null. Cash и фактические виртуальные количества — состояние журнала, не оценка по новой цене. Каждая точка истории несёт свой timestamp и не становится текущей оценкой. Нельзя интерполировать через пропуски для положительных выводов.

`fees_usdt` — исходная оценка fee_quote, включая перевод buy fee в acquired base по reference mid. `buffer_usdt` — adverse buffer; `spread_impact_usdt` — отдельная оценка book spread/impact. Не списывать эти расходы повторно из уже net Equity. `turnover_usdt` — сумма gross_qty × reference_mid; `estimated_legs` не равно числу закрытых round trips.

Profit Factor null с LOT_REALIZATION_NOT_DEFINED: исходный портфельный движок не определяет закрытые лотовые round trips. Это поле нельзя показывать как0 или бесконечность. Полные research-атрибуции BTC/ETH/SOL, Buy&Hold/Random и статистические проверки остаются в CryptoLab.

## Когорта и приёмка

Три независимые системы по300 USDT с единым будущим T0. T01/STRESS — внутренние контроли R2, не дополнительные участники и не добавочный общий капитал. Исходная работающая v3.2 не сбрасывается; для строгого сравнения нужен новый Native cohort account. Через7/30 дней — только промежуточное сравнение; R2 требует шесть полных календарных месяцев.

Нужны consumer schema/age/null tests и проверка read-only permissions до активации интерфейса. Согласование этого wire-format приёмником остаётся отдельным Gate. Example синтетический, не доходность и не факт запуска.
