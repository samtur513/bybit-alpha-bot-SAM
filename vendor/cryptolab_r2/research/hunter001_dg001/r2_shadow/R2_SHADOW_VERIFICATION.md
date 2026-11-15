# R2-SHADOW — локальная проверка и доказательства

## Вердикт

**OFFLINE_PREPARED / VPS_PENDING**, если `evidence/VERIFICATION.json` содержит успешный итог. Не DEPLOYED. Локальные данные синтетические/контрольные; новых биржевых запросов, реальных/Demo-ордеров, установок, SSH, изменений systemd/VPS/v3.2/8083 нет.

Источник: CryptoLab `815d6884980791309cb37770ac96a9a93a2c48c0`. Все исходные файлы остаются в родительском дереве коммита; изменения добавлены только в `research/hunter001_dg001/r2_shadow/`. `FROZEN_MANIFEST.json` фиксирует9 runtime-файлов с исходным Git blob SHA и SHA256. Remote compare должен показывать исключительно добавление этого каталога. R2-DEMO не импортируется и не изменяется.

## Воспроизведение без установок

Из корня checkout CryptoLab на Python3.12 с уже имеющимися NumPy2.5.4 и pytest:

```text
python research/hunter001_dg001/r2_shadow/verify.py
```

Команда выполняет только локальные тесты/упаковку. Сеть в дочерних тестовых процессах запрещена через socket layer. Предполагается доступный временный каталог внутри checkout. Evidence содержит stdout/stderr, JUnit, параметры платформы, raw-resource profile, recovery receipt, synthetic/null telemetry, состав runtime ZIP и SHA256. `SHA256SUMS.txt` проверяет все файлы нового каталога, кроме самого checksum-файла, cache и тестового network guard; не содержит циклический self-hash.

Детерминированы seed и состав runtime-архива, ZIP timestamps, исходные данные тестов. Измеренное время, временные пути JUnit, receipt UUID и timestamp могут различаться; они не являются экономическим результатом. Одинаковый код/конфигурации дают одинаковый runtime ZIP. Повторная упаковка не сохраняет prospective capital или результаты: в ZIP их нет.

## Выполненные проверки

| Область | Доказательство |
|---|---|
| Регрессия оригинала | 37 исходных тестов без редактирования test_validation.py |
| Прежний интеграционный слой | 34 интеграционных/граничных теста |
| QA FIX-001 | 49 тестов текущей оценки, evidence Gate и nonzero recovery |
| Стратегия | Все8 масок eligibility;100 фиксированных случайных историй, сравнение с независимым parent factors; ties, zero-vol, one-ULP |
| Исполнение/24 профиля | Три последовательных дня:3/2/0 eligible; исходный Observer и адаптер дают одинаковые accounts/decisions/ORDER events |
| Комиссии/ограничения | Исходные BASE/STRESS/MODEL_C5/OBSERVED_BOOK тесты: buy fee in base, sell fee in quote, precision/min/max/depth/dust, no borrowing, никакого fill claim |
| Независимая бухгалтерия | Recovery заново проходит cash/qty chain ORDER от первоначального капитала и сверяет checkpoint; искусственная подмена баланса блокируется |
| Сбой до COMMIT | Исключение при последнем EXECUTION event откатывает все24 счёта/события |
| Сбой после COMMIT | Ошибка CSV-export не дублирует операции при повторном execute |
| Аварийный выход | Отдельный процесс завершается os._exit внутри незавершённой SQLite-транзакции; recovery возвращает исходные24 счета |
| Raw | Пустые/бинарные/64KiB/>1MB bodies, hash/byte equality, restart, duplicate id, API/JSON errors, append-only triggers, corruption |
| Raw crash recovery | Сбой после raw COMMIT до удаления manifest и после удаления manifest до удаления spool; дубликат не создаётся |
| Seal | SHA256 целого закрытого сегмента, отказ последующей записи, обнаружение tamper |
| Safety | Только allowlisted GET api.bybit.com; order/account/demo paths отклонены до транспорта; redirects запрещены; closed OOS guard сохранён |
| Авторизация | false policy отклоняет запуск до создания data-dir/DB/HTTP; неверный T0/капитал/участники блокируются |
| Телеметрия | База читается mode=ro одним snapshot; null при stale; R2−T01 только для одинаковой операции; output path не может перезаписать DB |
| Код/среда | Git blob/SHA256 parity; источник после подмены не импортируется; -O запрещён |

Фактические counts/failures/errors — в JUnit и VERIFICATION.json. Ранний прогон исходных тестов встретил запрет стандартного Temp-каталога Windows: после переноса basetemp в рабочую папку все37 прошли. При разработке тестов исправлены неправильное обращение к parent factors и слишком длинный pytest parameter id; стратегические файлы не менялись. Обнаруженная разница gzip representation для одинаковых raw bytes учтена в идемпотентности: сохраняется первая валидная запись, исходные bytes и receipt обязаны совпадать.

Проверка wire-format: `check_contract.py` принимает2 корректных примера и отклоняет6 примеров с лишним приватным полем, неверной версией, отрицательным cash, отсутствующим профилем, boolean вместо order counter и неверными весами. Это локальная проверка фиксированного набора используемых JSON Schema keywords, не полный Draft202012 validator. Consumer schema/age/permission acceptance остаётся Gate. Никаких библиотек для этой проверки не устанавливалось.

## Ресурсное измерение и пределы доказательств

4410 синтетических ответов сохраняются полностью в одном дневном SQLite raw-файле. `RESOURCE_PROFILE.json` содержит реальный размер, время и Python allocation peak. Это нагрузка на локальном Windows SSD; не live VPS benchmark. Время включает синхронную запись и overhead измерителя, не равно CPU time. RSS/сетевой RTT/чужие процессы не измерялись. Нельзя обещать 8GiB/192MB верхнюю границу по этому примеру; лимиты останавливают дальнейший сбор, а не гарантируют непрерывность.

Не проверены Linux OS-lock на целевой машине, power-loss durability оборудования, backup/restore производственной среды, реальный egress firewall, действующие Spot metadata, часы/сетевые условия VPS, интеграция в запущенную панель и совместное влияние трёх процессов. Эти пункты составляют VPS Gate.

Нулевые order counters подтверждены конструкцией capability и локальным тестом с отключённой сетью. Это не заявление об аудите всех внешних биржевых аккаунтов пользователя. Система не читает ключи/аккаунты и не обращается к Demo API.

## Экономическая интерпретация

Сохранён исходный шестимесячный протокол и прежний NO-GO. Историческая проблема turnover comparability Random не исчезает от инфраструктурной интеграции. Новое перспективное исследование не начато. Отчёты7/30 дней промежуточные; экономическое подтверждение требует шесть полных календарных месяцев, data/execution/risk audit, Buy&Hold/Random comparability, парные блоки7/30 дней и исходную коррекцию множественных сравнений. Положительный PnL сам по себе не доказывает преимущество R2 над T01.

## QA FIX-001 — воспроизведение, исправления и дополнительные evidence

Начальное состояние проверено относительно a2f1820d9b6da02c3f24cc8a5c1680016ea2b54e. Два минимальных теста воспроизвели замечания до изменения реализации: свежая минутная котировка при старой часовой Equity давала null; разрешающие флаги без результатов пропускали выполнение до process lock. Исходный red JUnit сохранён в evidence/QA_FIX_001_REPRO_BEFORE.xml как ожидаемое доказательство прежней ошибки, не итоговый verdict.

Итоговые группы:37 исходных +34 прежних интеграционных +49 QA FIX тестов =120. Точные pass/failure/error counts — в evidence/VERIFICATION.json и трёх JUnit. Все дочерние проверки выполняются при отключённой сети. Полный повтор: python research/hunter001_dg001/r2_shadow/verify.py. Он также повторяет4410-response resource experiment и заново собирает runtime ZIP.

Новые тесты:
- 60 минут через исходный Observer.tick: ровно60 исходных poll,24 часовых Equity, свежие текущие Equity/PnL/R2−T01 всю проверенную последовательность; export не меняет ни одну таблицу.
- Source timestamps0/120/121 секунд и будущее время; экспорт не обновляет возраст котировки.
- Неполные/перепутанные/будущие/устаревшие/crossed/wrong-mid данные не принимаются.
- Один read-only snapshot остатков для всех профилей; история и risk/accounting не переписываются. При пустом cache даже имеющийся journal mark не подставляется в production current valuation.
- Gate проверяет полный packet на синтетической локальной fixture, затем отклоняет wrong host/platform/path/cohort/source hash, missing/tampered files, failed Linux exit/probe/trace, altered functional balance, потерю raw, истёкшие/будущие результаты, недостаточные измеренные или текущие ресурсы, отсутствие user approval или pinned manifest SHA.
- Даже все true-флаги не позволяют дойти до lock/start receipt/network без readiness. Сбор readiness без отдельного разрешения не создаёт каталогов.

### Recovery с ненулевой бухгалтерией

recovery_scenario.py — отдельный воспроизводимый офлайн-сценарий. Он использует неизменённые frozen decide/execute/trade_account и синтетические входы, явно помеченные SYNTHETIC_OFFLINE_NOT_EXCHANGE_DATA. До сбоя у всех24 профилей есть реальные для виртуального журнала ненулевые BUY/SELL legs; контрольный двухдневный прогон содержит132 ORDER events.

Сценарии:
1. Дочерний процесс завершается os._exit(23) внутри настоящего frozen execute после записи EXECUTION event, до COMMIT. Все незавершённые изменения откатываются;24 checkpoints/events/decisions совпадают с состоянием до сбоя.
2. Дочерний процесс завершается os._exit(24) в export после COMMIT. Все24 checkpoints и журнал уже совпадают с непрерывным контрольным прогоном.
3. Recovery сверяет независимую cash/qty цепочку, raw references и все24 счёта; retry даёт тот же контрольный итог, повторный execute идемпотентен.

evidence/RECOVERY_NONZERO.json содержит exit codes, counts и результаты/хеш каждого из24 профилей в обоих случаях. evidence/RECOVERY_NONZERO_INPUTS_AND_LEDGERS.zip содержит все исходные synthetic inputs/raw, независимые SQLite journals/checkpoints, CSV и ALL_24_ACCOUNTS.json с полными состояниями до/после каждого сбоя. SHA256 архива и отчёта входит в SHA256SUMS. Ручной повтор: python research/hunter001_dg001/r2_shadow/recovery_scenario.py --output <NEW_LOCAL_DIRECTORY>.

Ненулевые events здесь — бумажные оценки по synthetic fixtures, а не биржевые ордера/доходность. Сеть запрещена в CLI-сценарии и в тестовых дочерних процессах.

### Границы утверждений

Положительные readiness tests используют SYNTHETIC_TEST_HOST_NOT_VPS и временные fixtures; они проверяют валидатор и не являются фактическим VPS PASS. Linux compatibility/functional/resource packet на VPS в этой работе не собирался. Default AUTHORIZATION false/null, фактические readiness files отсутствуют. Отсутствие фактических результатов намеренно блокирует будущий run. Это OFFLINE_PREPARED / VPS_PENDING, не DEPLOYED.

Исходные9 frozen runtime-файлов, оба стратегических протокола, T01 и R2-DEMO не менялись. Формат телеметрии/schema/публичный TORG_BOT в этом QA не менялись. SSH/VPS/systemd/install/service/real-order/Demo-order actions:0.
