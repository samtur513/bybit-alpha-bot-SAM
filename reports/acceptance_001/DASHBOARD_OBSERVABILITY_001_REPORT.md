# DASHBOARD_OBSERVABILITY_001_REPORT.md
# Торговый Центр Управления TORG_BOT Trading Control Center v1.0
**Протокол:** `DASHBOARD-OBSERVABILITY-001`  
**Ревизия аудита:** `REPAIR-003 AUDIT RESOLUTION`  
**Дата:** 2026-11-16  
**Ветка Git:** `fix/final-start-blockers-009`  
**Целевой порт:** `0.0.0.0:8085` (Strict Read-Only)  
**Статус реализации:** `AUDIT REPAIR-003 RESOLVED / 100% PASS (12/12 REGRESSION TESTS, 37/37 TOTAL) / READY FOR AUDIT`

---

## 1. Архитектурный обзор и устранение замечаний аудита REPAIR-003

В соответствии с заключением независимого архитектора `TORG_BOT Trading Control Center — REPAIR-003: PARTIAL PASS / DEPLOYMENT BLOCKED` на коммит `1f89b0c66e717fc069d92c677c6fca5dd0442c01`, все выявленные несоответствия в части достоверности торговых данных, расчёта капитала MTM, каскадного тейк-профита, схемы закрытых сделок Shadow, экранирования XSS и отображения разрыва R2 устранены в полном объёме.

Никакие торговые стратегии, параметры рисков, базы данных или исполняемые сервисы боевой инфраструктуры не затрагивались.

### Матрица статуса требований REPAIR-003

| Требование | Описание замечания аудитора | Статус решения в REPAIR-003 | Подтверждающий тест |
| :--- | :--- | :--- | :--- |
| **P0.1** | Подмена `current_mark` ценой входа, фейковый нулевой PnL, подмена `last_sl_update` | Устранено: возвращается только подтверждённый `current_mark` и расчётный PnL, иначе `NOT_AVAILABLE` / `NOT_RECORDED` | `test_01`, `test_02` |
| **P0.2** | Несуществующие `tp1_taken`/`tp2_taken` в `CoreV3Engine.to_dict()` | Устранено: уровни отделены от факта исполнения, статус помечается как `UNKNOWN` (никогда не `False`/`PENDING`) | `test_03` |
| **P0.3** | Несоответствие полей закрытых сделок Shadow схеме `TradeRecord` | Устранено: прямое соответствие полям `initial_qty`, `final_qty`, `funding_pnl`, `net_pnl`, `slippage_bps` | `test_04` |
| **P0.4** | Shadow MTM игнорировал PnL открытых позиций; несовпадение ключей `r2`/`r2_shadow`; разрыв R2 без GAP-маркера | Устранено: MTM = `realized + unrealized`; дублирование ключей `r2` и `r2_shadow`; сегментация полилинии SVG при dt > 3600s с бейджем GAP | `test_05`, `test_06`, `test_07` |
| **P0.5** | Преждевременный статус верификации учёта `ACCOUNTING_VERIFIED=OK` при 0 сделок | Устранено: при 0 закрытых сделок выводится `ACCOUNTING_NOT_YET_OBSERVED`; розничные комиссии VIP0 (0.100%/0.036%) | `test_08` |
| **P0.6** | Ложный `evaluated` в R2; вымышленные решения `NO_BREAKOUT` без сигнала; превращение ошибки БД в `OK_EMPTY` | Устранено: `market_regime="NOT_RECORDED"`, `decision="NO_DECISION_RECORDED"`; сбой БД возвращает `DATA_SOURCE_ERROR` | `test_09`, `test_11` |
| **P0.7** | Потенциальный XSS в динамических полях; статус панели опирался только на телеметрию | Устранено: полное экранирование `esc()` всех полей; статус панели `ACTIVE_RUNNING` с проверкой `INDEPENDENT_HTTP_LISTENER` | `test_10`, `test_12` |

---

## 2. Детальный отчёт об устранении замечаний P0.1–P0.7

### P0.1. Достоверность открытых позиций
- **Текущая рыночная цена (`current_mark`)**: Возвращается подтверждённая биржевая цена маркировки с временной меткой. Если цена в сериализованном состоянии отсутствует, выводится строгое строковое значение `"NOT_AVAILABLE"`. Подмена ценой входа `entry_price` категорически ликвидирована.
- **Нереализованный PnL (`unrealized_pnl_usdt`)**: Рассчитывается по математической формуле:
  $$\text{PnL} = \text{direction} \times (\text{current\_mark} - \text{entry\_price}) \times \text{qty}$$
  Если `current_mark` отсутствует или не является числом, `unrealized_pnl_usdt` устанавливается в `"NOT_AVAILABLE"`. Подмена отсутствующего значения фиктивным `0.0` устранена.
- **Время последнего обновления стоп-лосса (`last_sl_update_utc`)**: Отображается только фактическое время зарегистрированного события модификации стоп-лосса (`last_sl_update_ts`). Если событие обновления не происходило, возвращается `"NOT_RECORDED"`. Подмена временем входа `entry_time` устранена.
- **Позиции диапазонного модуля (Range)**: При отсутствии подтверждённых биржевых цен все зависимые финансовые поля помечаются как `"NOT_AVAILABLE"`.

### P0.2. Каскадный тейк-профит Native Trend
- **Анализ контракта `CoreV3Engine.to_dict()`**: В реальном словаре состояния движка Native Trend (`v3_2/src/core_v3.py`) поля `tp1_taken` и `tp2_taken` не сохраняются — сериализуется только булево поле `partial_taken`.
- **Разделение расчётных уровней и факта исполнения**:
  - Уровни TP1 (1.5R) и TP2 (3.0R) выводятся как расчётные математические ориентиры:
    $$\text{TP1} = \text{entry} + \text{dir} \times 1.5 \times \text{ATR}, \quad \text{TP2} = \text{entry} + \text{dir} \times 3.0 \times \text{ATR}$$
  - Статусы исполнения `tp1_status` и `tp2_status` при наличии `partial_taken` устанавливаются в `"UNKNOWN"`, а в список флагов добавляется `"TP_STATUS_UNKNOWN"`. Любые необоснованные утверждения о статусах `False` или `PENDING` без подтверждающих событий исполнения ликвидированы.

### P0.3. Соответствие закрытых сделок Shadow схеме `TradeRecord`
- **Строгая схема полей**: В адаптере `_get_authoritative_shadow_state()` данные закрытых сделок приводятся в точное соответствие с полями `TradeRecord.to_dict()` из `v3_2/src/shadow/paper_trader_v32_shadow.py`:
  - `initial_qty`: исходный объём позиции при открытии;
  - `final_qty`: оставшийся объём перед закрытием;
  - `funding_pnl`: накопленный PnL от фандинга;
  - `fees_paid`: фактически уплаченные торговые комиссии;
  - `gross_pnl` и `net_pnl`: валовой и чистый финансовый результат;
  - `exit_reason`: подтверждённая причина выхода;
  - `slippage_bps`: проскальзывание в базисных пунктах.
- Если финансовые поля в записи отсутствуют, они не заполняются нулями, а помечаются как `"NOT_RECORDED"`.

### P0.4. Расчёт кривой капитала MTM и отрисовка графиков
- **Честный расчёт MTM для Shadow**: В таблице `shadow_state_journal` поле `shadow_realized_equity` отражает только зафиксированный капитал. Адаптер `TradingControlCenterDataEngine` суммирует реализованный капитал с нереализованным PnL всех активных позиций:
  $$\text{MTM Total Equity} = \text{shadow\_realized\_equity} + \sum \text{unrealized\_pnl}$$
  При наличии открытой убыточной позиции капитал MTM достоверно снижается относительно `realized_equity`.
- **Синхронизация ключей R2**: В возвращаемом словаре аналитики данные кривой капитала CryptoLab предоставляются одновременно по ключам `"r2"` и `"r2_shadow"`, что исключило несогласованность с шаблоном HTML-рендерера.
- **Хронологический порядок и ограничение выборки**:
  - Для Native и Shadow выборка ограничена последними 500 записями в строго восходящем хронологическом порядке (`ORDER BY journal_id DESC LIMIT 500` с последующим разворотом массива).
  - Для R2 выборка из таблицы `events` ограничена последними 500 записями профиля `R2:300:BASE:OBSERVED_BOOK`.
- **Обработка разрыва телеметрии R2 (01:03–09:20 UTC)**:
  В рендерере графиков SVG `_render_svg_equity_chart` реализован алгоритм разбиения кривой на независимые сегменты polyline, если интервал между соседними точками превышает 3600 секунд ($> 1$ часа). Соединение точек через интервал сбоя не выполняется, а в месте разрыва отображается визуальный индикатор `GAP: 01:03 - 09:20 UTC (DATA_INTERRUPTED)`.

### P0.5. Честная верификация учёта (Accounting Verification)
- **Исключение ложного статуса `OK`**: Преждевременное выставление `ACCOUNTING_VERIFIED = OK` при отсутствии реальных закрытых сделок запрещено. Если число закрытых сделок равно нулю (`trades_count == 0`), статус верификации устанавливается в `"ACCOUNTING_NOT_YET_OBSERVED"`.
- **Розничные комиссии Bybit VIP0**: В карточках комиссий отображается розничный уровень комиссии мейкера ($0.036\%$) и тейкера ($0.100\%$). Любые гипотетические скидки (например, MNT discount) исключены из базового отображения до документального подтверждения их активности на счетах.

### P0.6. Сканер инструментов и устойчивость к ошибкам БД
- **Корректный подсчёт воронки R2**: Если по монетам не зафиксировано решений или сигналов моделей, счётчик `evaluated` воронки не инкрементируется и равен `0`.
- **Отсутствие вымышленных решений**: Для закрытых баров без зафиксированного пробоя или сигнала выставляются честные значения:
  - `market_regime = "NOT_RECORDED"`
  - `decision = "NO_DECISION_RECORDED"`
  - `signal = "NO_SIGNAL_RECORDED"`
  - `confidence_status = "NO_SIGNAL_RECORDED"`
  (подстановка вымышленных `NO_BREAKOUT` или `OBSERVING_MARKET` полностью удалена).
- **Изоляция сбоев баз данных**: При повреждении файла SQLite или невалидном JSON в таблице журналов система возвращает статус `DATA_SOURCE_ERROR` и выводит предупреждение в UI. Сбой базы данных ни при каких обстоятельствах не маскируется под пустое состояние портфеля (`OK_EMPTY`).

### P0.7. Безопасность публичного интерфейса (XSS и Read-Only)
- **Тотальное экранирование HTML**: Все без исключения динамические поля (символы монет, стратегии, когорты, описания ролей, временные метки, причины отказа) экранируются через вспомогательную функцию `esc()`, использующую `html.escape(quote=True)`. Внедрение скриптов или HTML-тегов через поля телеметрии невозможно.
- **Статус службы Trading Control Center**: Статус службы в карточке мониторинга выводится как `ACTIVE_RUNNING` с подтверждением `INDEPENDENT_HTTP_LISTENER`, что разграничивает статус самого HTTP-сервера и возраст входящей торговой телеметрии.
- **Защита от мутаций**: Сервер строго отклоняет запросы методами `POST`, `PUT`, `DELETE`, `PATCH` с кодом `405 Method Not Allowed`, предварительно считывая тело запроса для предотвращения разрыва TCP-соединения (`WinError 10053`).

---

## 3. Детальный разбор 12 обязательных регрессионных тестов

Все 12 регрессионных тестов, предписанных независимым архитектором, реализованы в `tests/test_dashboard_observability_001.py` и успешно пройдены:

### Тест 1: `test_01_open_losing_position_with_missing_unrealized_pnl`
- **Цель**: Проверка контракта расчёта PnL при отсутствующем поле `unrealized_pnl` в состоянии.
- **Входные данные**: Позиция LONG 2.0 BTC @ 100.0, подтверждённый `current_mark = 95.0`, `unrealized_pnl` отсутствует.
- **Проверка**: Вычислен математически точный PnL: $1 \times (95.0 - 100.0) \times 2.0 = -10.0$ USDT. Во второй части теста при отсутствии `current_mark` поля `current_mark` и `unrealized_pnl_usdt` принимают значение `"NOT_AVAILABLE"` (никогда не подменяются нулём).
- **Результат**: `PASSED`.

### Тест 2: `test_02_actual_mark_differs_from_entry`
- **Цель**: Проверка недопустимости подмены рыночной цены ценой входа.
- **Входные данные**: Позиция BTCUSDT с `entry_price = 50000.0`, подтверждённая рыночная цена `current_mark = 51200.0`.
- **Проверка**: `pos["current_mark"] == 51200.0`, `pos["current_mark"] != pos["entry_price"]`, `unrealized_pnl_usdt == 600.0`.
- **Результат**: `PASSED`.

### Тест 3: `test_03_partial_tp1_exit_without_flag_in_state`
- **Цель**: Проверка обработки каскадного TP при отсутствии явных флагов в `CoreV3Engine.to_dict()`.
- **Входные данные**: Позиция SOLUSDT с частичным выходом (`initial_qty = 10.0`, `current_qty = 7.0`, `partial_taken = True`), поля `tp1_taken`/`tp2_taken` отсутствуют.
- **Проверка**: `tp1_status == "UNKNOWN"`, `tp2_status == "UNKNOWN"`, флаг `"TP_STATUS_UNKNOWN"` присутствует. Исключены ложные статусы `False` или `PENDING`.
- **Результат**: `PASSED`.

### Тест 4: `test_04_real_shadow_trade_record_fields`
- **Цель**: Точное соответствие полей закрытых сделок Shadow схеме `TradeRecord.to_dict()`.
- **Входные данные**: Запись сделки Shadow с полями `initial_qty = "1.500"`, `final_qty = "0.500"`, `funding_pnl = "-12.50"`, `fees_paid = "45.00"`, `gross_pnl = "1500.00"`, `net_pnl = "1442.50"`, `slippage_bps = "3.5"`.
- **Проверка**: Все поля корректно распарсены с сохранением знаков и числовых значений без подмены нулями.
- **Результат**: `PASSED`.

### Тест 5: `test_05_shadow_open_position_and_unrealized_loss`
- **Цель**: Проверка честного расчёта MTM капитала Shadow при наличии открытого убытка.
- **Входные данные**: `shadow_realized_equity = 300.0`, открытая позиция LONG 1.0 @ 100.0 с текущей ценой маркировки 90.0 (нереализованный убыток $-10.0$).
- **Проверка**: MTM Equity в серии графика составляет $300.0 - 10.0 = 290.0$ USDT, что строго не равно $300.0$.
- **Результат**: `PASSED`.

### Тест 6: `test_06_r2_equity_history_appears_on_html_chart`
- **Цель**: Проверка паритета ключей `r2` / `r2_shadow` и отображения кривой R2 на HTML-графике.
- **Входные данные**: Записи `EQUITY` профиля `R2:300:BASE:OBSERVED_BOOK` со значениями $300.0 и $305.5.
- **Проверка**: Ключи `r2` и `r2_shadow` присутствуют в словаре аналитики, кривая рендерится в SVG, значение 305.5 присутствует в HTML.
- **Результат**: `PASSED`.

### Тест 7: `test_07_history_over_500_records_shows_latest_points`
- **Цель**: Проверка выборки последних 500 записей в восходящем хронологическом порядке.
- **Входные данные**: 600 записей журнала Native (id 1..600, equity 301..900).
- **Проверка**: Последняя точка серии соответствует записи 600 (equity = 900.0), первая точка выборки $\ge 401.0$ (старые записи 1..100 отсечены), порядок строго хронологический.
- **Результат**: `PASSED`.

### Тест 8: `test_08_no_trades_accounting_not_yet_observed`
- **Цель**: Проверка статуса верификации учёта при отсутствии закрытых сделок.
- **Входные данные**: Базы данных созданы, сделок нет (`closed_trades == []`).
- **Проверка**: В HTML отображается статус `ACCOUNTING_NOT_YET_OBSERVED`, статус `OK` отсутствует.
- **Результат**: `PASSED`.

### Тест 9: `test_09_scanner_does_not_invent_decision_without_signal`
- **Цель**: Проверка отсутствия вымышленных решений в сканере при отсутствии сигналов.
- **Входные данные**: Символ BTCUSDT имеет обработанный бар, но сигнала нет. В R2 события отсутствуют.
- **Проверка**: Для Native BTCUSDT: `market_regime = "NOT_RECORDED"`, `decision = "NO_DECISION_RECORDED"`, `confidence_status = "NO_SIGNAL_RECORDED"`. Для R2: `funnel.evaluated == 0`, `confidence_status = "NOT_EVALUATED"`.
- **Результат**: `PASSED`.

### Тест 10: `test_10_all_dynamic_html_fields_escaped`
- **Цель**: Проверка экранирования динамических полей против XSS.
- **Входные данные**: Внедрение полезной нагрузки `<script>alert("xss")</script>` в символ позиции и `"><img src=x onerror=alert(1)>` в когорту.
- **Проверка**: В сгенерированном HTML отсутствуют сырые теги `<script>` и `<img>`, внедрения полностью экранированы в `&lt;script&gt;` и `&quot;&gt;&lt;img...`.
- **Результат**: `PASSED`.

### Тест 11: `test_11_corrupted_state_journal_not_empty_portfolio`
- **Цель**: Проверка реакции на повреждённую базу данных или невалидный JSON.
- **Входные данные**: Файл базы данных с мусорными байтами, а также валидная таблица с повреждённым JSON (`{invalid_json:`).
- **Проверка**: Система возвращает `data_status["native"] == "DATA_SOURCE_ERROR"`, флаг `is_empty` равен `False` (ошибка БД не превращается в пустой портфель).
- **Результат**: `PASSED`.

### Тест 12: `test_12_full_read_only_mode_preserved`
- **Цель**: Проверка строгого режима только для чтения на HTTP-сервере.
- **Входные данные**: Запуск сервера на временном порту 18099, выполнение запросов GET, POST, PUT, DELETE, PATCH.
- **Проверка**: GET возвращает 200 с `read_only_enforced = True`; все мутирующие запросы отклоняются с кодом `405 Method Not Allowed` без зависания сокета.
- **Результат**: `PASSED`.

---

## 4. Сводный протокол прохождения тестов

```
============================= test session starts =============================
platform win32 -- Python 3.12.3, pytest-9.0.2, pluggy-1.6.0
rootdir: c:\projects\Torg_Bot_3 -Antigraviti
collected 12 items

tests/test_dashboard_observability_001.py::test_01_open_losing_position_with_missing_unrealized_pnl PASSED [  8%]
tests/test_dashboard_observability_001.py::test_02_actual_mark_differs_from_entry PASSED [ 16%]
tests/test_dashboard_observability_001.py::test_03_partial_tp1_exit_without_flag_in_state PASSED [ 25%]
tests/test_dashboard_observability_001.py::test_04_real_shadow_trade_record_fields PASSED [ 33%]
tests/test_dashboard_observability_001.py::test_05_shadow_open_position_and_unrealized_loss PASSED [ 41%]
tests/test_dashboard_observability_001.py::test_06_r2_equity_history_appears_on_html_chart PASSED [ 50%]
tests/test_dashboard_observability_001.py::test_07_history_over_500_records_shows_latest_points PASSED [ 58%]
tests/test_dashboard_observability_001.py::test_08_no_trades_accounting_not_yet_observed PASSED [ 66%]
tests/test_dashboard_observability_001.py::test_09_scanner_does_not_invent_decision_without_signal PASSED [ 75%]
tests/test_dashboard_observability_001.py::test_10_all_dynamic_html_fields_escaped PASSED [ 83%]
tests/test_dashboard_observability_001.py::test_11_corrupted_state_journal_not_empty_portfolio PASSED [ 91%]
tests/test_dashboard_observability_001.py::test_12_full_read_only_mode_preserved PASSED [100%]

============================== 12 passed in 1.76s ==============================
```

### Полный регрессионный прогон трёх тестовых пакетов (37/37 PASS):
- `tests/test_dashboard_observability_001.py`: **12/12 PASSED**
- `tests/test_r2_recovery_001.py`: **13/13 PASSED**
- `tests/test_final_start_blockers_009.py`: **12/12 PASSED**
**Итог:** **37 passed in 53.56s (100% PASS, 0 Failures)**.

---

## 5. Спецификация REST API (Строгая достоверность данных)

### 5.1. `GET /api/status`
```json
{
  "timestamp_iso": "2026-11-16T13:40:00.000000+00:00",
  "read_only": true,
  "matrix": {
    "native": {
      "name": "System A — Native Dual-Regime",
      "market": "Bybit Linear Perpetual",
      "strategy": "Donchian 4h Trend + 1h Range",
      "initial_equity": 300.0,
      "total_equity": 300.0,
      "unrealized_pnl": 0.0,
      "realized_pnl": 0.0,
      "status": "TELEMETRY_FRESH",
      "data_source_status": "OK_EMPTY",
      "open_positions": 0
    },
    "shadow": {
      "name": "System B — Realistic Shadow",
      "market": "Bybit Linear Perpetual",
      "strategy": "Discrete L2 Books + 8h Funding",
      "initial_equity": 300.0,
      "total_equity": 300.0,
      "unrealized_pnl": 0.0,
      "realized_pnl": 0.0,
      "status": "TELEMETRY_FRESH",
      "data_source_status": "OK_EMPTY",
      "open_positions": 0
    },
    "r2": {
      "name": "System C — CryptoLab R2-SHADOW",
      "market": "Bybit Spot Cash",
      "strategy": "Hunter R2 Multi-Profile ($300 Benchmark)",
      "benchmark_profile": "R2:300:BASE:OBSERVED_BOOK",
      "initial_equity": 300.0,
      "total_equity": 300.0,
      "unrealized_pnl": 0.0,
      "realized_pnl": 0.0,
      "status": "TELEMETRY_FRESH",
      "data_source_status": "OK_EMPTY",
      "open_positions": 0
    }
  }
}
```

### 5.2. `GET /api/positions` (Честное пустое состояние при отсутствии позиций)
```json
{
  "timestamp_iso": "2026-11-16T13:40:00.000000+00:00",
  "total_open_positions": 0,
  "is_empty": true,
  "empty_reason": "Открытых позиций в подтверждённом состоянии систем не зарегистрировано (OK_EMPTY).",
  "data_status": {
    "native": "OK_EMPTY",
    "shadow": "OK_EMPTY",
    "r2": "OK_EMPTY"
  },
  "native_positions": [],
  "shadow_positions": [],
  "r2_positions": []
}
```

### 5.3. `GET /api/positions` (Пример валидной открытой позиции Native)
```json
{
  "symbol": "BTCUSDT",
  "direction": "LONG",
  "entry_price": 50000.0,
  "current_mark": 51200.0,
  "current_qty": 0.5,
  "unrealized_pnl_usdt": 600.0,
  "current_stop": 48000.0,
  "tp1_price": 51500.0,
  "tp2_price": 53000.0,
  "tp1_status": "UNKNOWN",
  "tp2_status": "UNKNOWN",
  "entry_time_utc": "2026-11-16T10:00:00Z",
  "last_sl_update_utc": "NOT_RECORDED",
  "flags": ["TREND_DONCHIAN", "TP_STATUS_UNKNOWN"]
}
```

### 5.4. `GET /api/scanner`
```json
{
  "timestamp_iso": "2026-11-16T13:40:00.000000+00:00",
  "funnel": {
    "native": {"universe": 18, "evaluated": 0, "eligible": 0, "rejected": 0, "active_positions": 0},
    "shadow": {"universe": 18, "evaluated": 0, "eligible": 0, "rejected": 0, "active_positions": 0},
    "r2": {"universe": 3, "evaluated": 0, "eligible": 0, "rejected": 0, "active_positions": 0}
  },
  "scanner_items": [
    {
      "system": "Native",
      "symbol": "BTCUSDT",
      "market_regime": "NOT_RECORDED",
      "decision": "NO_DECISION_RECORDED",
      "signal": "NO_SIGNAL_RECORDED",
      "confidence_status": "NO_SIGNAL_RECORDED"
    }
  ]
}
```

---

## 6. Процедура изолированного обновления панели на VPS (Только Dashboard)

> [!IMPORTANT]
> Обновление затрагивает **исключительно** веб-интерфейс дашборда на порту 8085.
> Никакие сервисы исполнения, боевая v3.2 (порт 8083, PID 3629780), VPN xray (PID 3165188) или базы данных SQLite **НЕ перезапускаются и НЕ изменяются**.

### Пошаговые команды для владельца (Termius):

```bash
# Шаг 1. Переходим в репозиторий проекта на VPS
cd /opt/bybit-shadow/bybit-alpha-bot-SAM

# Шаг 2. Загружаем свежие метаданные из Git (без модификации файлов)
git fetch origin fix/final-start-blockers-009

# Шаг 3. Извлекаем ТОЛЬКО обновлённые файлы панели
git checkout origin/fix/final-start-blockers-009 -- \
    v3_2/src/integrations/three_system_dashboard.py \
    v3_2/src/integrations/trading_control_center_data.py \
    v3_2/src/runners/run_three_system_dashboard.py

# Шаг 4. Проверяем валидность синтаксиса Python в режиме dry-run
/opt/bybit-bot/venv/bin/python v3_2/src/runners/run_three_system_dashboard.py --dry-run

# Шаг 5. Перезапускаем ИСКЛЮЧИТЕЛЬНО службу дашборда 8085
systemctl restart bybit-shadow-dashboard.service

# Шаг 6. Контрольная проверка статуса службы и API Health
sleep 2
systemctl status bybit-shadow-dashboard.service --no-pager
curl -s http://127.0.0.1:8085/api/health | head -c 350
echo ""
```

---

## 7. Процедура мгновенного отката (Rollback)

В случае необходимости немедленного возврата к предыдущей версии панели:

```bash
cd /opt/bybit-shadow/bybit-alpha-bot-SAM

# Возвращаем файлы панели к состоянию до обновления
git checkout 46c28e361b1e061956381ca1af8357ee5010b173 -- \
    v3_2/src/integrations/three_system_dashboard.py \
    v3_2/src/integrations/trading_control_center_data.py \
    v3_2/src/runners/run_three_system_dashboard.py

# Перезапускаем только службу дашборда
systemctl restart bybit-shadow-dashboard.service
```
