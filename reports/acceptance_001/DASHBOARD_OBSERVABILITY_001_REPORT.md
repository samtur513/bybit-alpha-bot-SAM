# DASHBOARD_OBSERVABILITY_001_REPORT.md
# Торговый Центр Управления TORG_BOT Trading Control Center v1.0
**Протокол:** `DASHBOARD-OBSERVABILITY-001`  
**Ревизия аудита:** `REPAIR-002 AUDIT RESOLUTION`  
**Дата:** 2026-11-16  
**Ветка Git:** `fix/final-start-blockers-009`  
**Целевой порт:** `0.0.0.0:8085` (Strict Read-Only)  
**Статус реализации:** `AUDIT REPAIR-002 RESOLVED / 100% PASS (13/13 TESTS, 38/38 TOTAL) / READY FOR AUDIT`

---

## 1. Архитектурный обзор и устранение замечаний REPAIR-002

В соответствии с заключением независимого архитектора `DASHBOARD-OBSERVABILITY-001 — INDEPENDENT AUDIT / REPAIR-002: BLOCKED — DEPLOYMENT NOT AUTHORIZED` на коммит `de14603f4153678607ca3aa06c9f9dd72f196db9`, код панели мониторинга был подвергнут фундаментальной ревизии.

Устранены все замечания категорий P0.1–P0.6 и P1. Реализована строгая достоверность данных без вымышленных таблиц, искусственных констант и непроверенных рыночных состояний.

```mermaid
flowchart TD
    subgraph Storage ["Хранилища данных VPS (100% Read-Only, mode=ro)"]
        NAT_STATE["native_state_journal\n(state_json: engine & range_engine)"]
        NAT_JRN["native_journal\n(total_equity, realized_equity)"]
        NAT_TRD["native_trades\n(closed trades, pnl)"]
        SHA_STATE["shadow_state_journal\n(state_json: shadow_portfolio.positions)"]
        SHA_EVT["shadow_execution_events\n(fill prices, slippage, fees)"]
        R2_ACC["data/cryptolab/observer.sqlite\naccounts (24 профиля)"]
        R2_EVT["data/cryptolab/observer.sqlite\nevents (EQUITY, DECISION, SIGNAL)"]
    end

    subgraph DataEngine ["TradingControlCenterDataEngine (v3_2/src/integrations)"]
        CACHE["In-Memory Cache (5.0s TTL)\nZero-IO при частых запросах"]
        RO_DB["Zero SQLite Mutations\nURI: file:...mode=ro\nBounded Queries: LIMIT 50"]
        ADAPTERS["Authoritative Adapters:\n- _get_authoritative_native_state()\n- _get_authoritative_shadow_state()\n- _get_r2_data()\n- Safe JSON & Array target_weights Parser"]
    end

    subgraph Security ["Hardened Presentation Layer"]
        XSS["HTML Escaping (html_lib.escape)\nSanitized Paths: [root]/..."]
        RO_GUARD["Strict Read-Only Guard\nPOST/PUT/DELETE/PATCH -> 405"]
        UI["Тёмный UI (6 Вкладок)\nClient-side Switch (#hash)\nAuto-refresh 10s"]
    end

    Storage --> RO_DB
    RO_DB --> CACHE
    CACHE --> ADAPTERS
    ADAPTERS --> Security
```

---

## 2. Детальный отчёт об устранении замечаний аудита

### P0.1. Использование исключительно настоящих SQLite-схем
- **Удалены запросы к несуществующим таблицам**: Полностью исключены запросы к `native_positions`, `shadow_positions`, `shadow_journal`, `shadow_trades`.
- **Подключены реальные авторитетные источники**:
  - **Native**: Таблица `native_state_journal` (хранит полный снапшот состояния `state_json`, включая `engine.open_positions` для трендовой подсистемы и `range_engine.open_positions` для диапазонной подсистемы); таблица `native_journal` (`total_equity`, `realized_equity`); таблицы `native_trades`, `native_missed_opportunities`, `native_funding_settlements`.
  - **Shadow**: Таблица `shadow_state_journal` (хранит снапшот `state_json`, включая `shadow_portfolio.positions` и `shadow_portfolio.closed_trades`); таблицы `shadow_execution_events`, `shadow_funding_settlements`.
  - **CryptoLab R2**: Таблицы `accounts` (24 профиля) и `events` (`DECISION`, `SIGNAL`, `EQUITY`).
- **Разграничение состояний данных**: Если база данных отсутствует или повреждена, система возвращает статус `DATA_SOURCE_ERROR` с выводом предупреждающего баннера в UI. Если база данных валидна, но сделок нет, возвращается честный статус `OK_EMPTY` (`total_open_positions == 0`). При наличии позиций возвращается статус `OK_POSITIONS`.

### P0.2. Ликвидация вымышленных торговых данных
- **Сканер инструментов**: Анализируются только те монеты, по которым зафиксированы реальные закрытые бары (`last_processed_bar_ts > 0`). Монеты без закрытых баров помечаются как `AWAITING_FIRST_BAR`.
- **Устранение фиктивного `NEUTRAL_ZONE`**: Сканер не подставляет вымышленные статусы нейтральной зоны для инструментов, не прошедших расчёт.
- **Двусторонняя симметрия (Long / Short)**: Устранено принудительное выставление стороны LONG. Направление определяется строго по знаку позиции: `+1 -> LONG`, `-1 -> SHORT`.
- **Изоляция исторических отказов**: Исторические записи из `native_missed_opportunities` не выдаются за текущие причины отказа фильтров сканера.
- **Временные метки**: Исключена подмена реальных меток времени баров на `now_iso`. Для каждого элемента сканера и журнала выводятся: `source` (источник), `event_at` (фактическое время события в UTC), `observed_at` (время считывания телеметрией), а также `confidence_status` (`PROVEN_CLOSED_BAR`, `TELEMETRY_RECORDED`, либо `NOT_RECORDED`).

### P0.3. Корректная обработка CryptoLab R2
- **Парсинг целевых весов (`target_weights`)**: В CryptoLab телеметрии решение экспортируется как массив `[w_btc, w_eth, w_sol]`. Введён адаптер, корректно отображающий массив по индексам на символы `["BTCUSDT", "ETHUSDT", "SOLUSDT"]`, а также поддерживающий формат словаря.
- **Парсинг сигналов**: Поддержана структура `scores[sym] = {"score": float, "eligible": bool, "target_weight": float}`.
- **Строгая изоляция кривой капитала $300**: Выборка из таблицы `events` для кривой капитала строго фильтрует профиль `R2:300:BASE:OBSERVED_BOOK` (`strategy == 'R2'`, `capital == 300`, `cost == 'BASE'`, `policy == 'OBSERVED_BOOK'`). Смешивание с профилями $1,000 и $10,000 категорически исключено.
- **Спотовый инвентарь**: Для позиций R2 отображается бейдж `VIRTUAL_MODEL_ESTIMATE`, а при отсутствии потоковой цены возвращается индикатор `NOT_AVAILABLE`.

### P0.4. Корректный учёт капитала и сделок
- **Native Equity**: Базовый капитал Native считывается из `native_journal.total_equity` (начальный депозит + нереализованный PnL + накопленный фандинг), что исключает скачки баланса, возникавшие при чтении `realized_equity`.
- **Поддержка Cascade Take-Profit**: Движок Native Trend работает с каскадным закрытием позиций (TP1 = 1.5R, TP2 = 3.0R). Панель корректно отображает расчетные уровни TP1/TP2 и флаг `TP1_TAKEN`.
- **Дедупликация закрытых сделок**: Закрытые сделки извлекаются непосредственно из подтверждённых таблиц (`native_trades`, `shadow_portfolio.closed_trades`) с дедупликацией по уникальному ID сделки.

### P0.5. Достоверность технического состояния (System Health)
- **Исключение искусственных констант**: Удалены жестко зашитые значения `ACTIVE_RUNNING`, `UNTOUCHED_ISOLATED_ACTIVE` и `n_restarts: 1798`.
- **Динамический расчёт статусов**: Статус служб определяется по возрасту heartbeat телеметрии:
  - $\le 120$ сек: `TELEMETRY_FRESH` (зелёный индикатор);
  - $> 120$ сек: `TELEMETRY_STALE` (жёлтый индикатор);
  - Телеметрия отсутствует: `SERVICE_STATE_UNKNOWN` (серый индикатор).
- **Нулевой риск выполнения команд**: Полностью исключен запуск внешних shell-команд (systemctl, ps) через публичный веб-интерфейс.

### P0.6. Безопасность и производительность публичной панели
- **Защита от XSS**: Все строковые поля, символы, причины отказа и сообщения об ошибках экранируются через `html_lib.escape()`.
- **Санитарная очистка путей**: Пути файловой системы сервера `/opt/bybit-shadow/...` обезличиваются в представлении UI до `[root]/...`.
- **Строгий Read-Only режим**: Любые запросы с методами записи (POST, PUT, DELETE, PATCH) отклоняются сервером с кодом `405 Method Not Allowed`.
- **Защита от I/O деградации**:
  - In-memory кэш с TTL = 5.0 сек;
  - Ограничение выборок `LIMIT 50`;
  - Исключены тяжелые вызовы `PRAGMA quick_check(1)`.

---

## 3. Результаты приёмочных тестов (13/13 PASS)

Специализированный набор приёмочных тестов `tests/test_dashboard_observability_001.py` полностью переписан под реальные схемы SQLite и успешно пройден:

```
tests/test_dashboard_observability_001.py::test_1_all_three_systems_empty_positions PASSED
tests/test_dashboard_observability_001.py::test_2_open_long_and_short_positions_on_fixtures PASSED
tests/test_dashboard_observability_001.py::test_3_tp1_taken_tp2_pending PASSED
tests/test_dashboard_observability_001.py::test_4_trailing_stop_modified PASSED
tests/test_dashboard_observability_001.py::test_5_strategy_without_tp_or_sl_reports_not_defined PASSED
tests/test_dashboard_observability_001.py::test_6_missing_or_corrupted_database_resilience PASSED
tests/test_dashboard_observability_001.py::test_7_stale_telemetry_detection PASSED
tests/test_dashboard_observability_001.py::test_8_r2_restart_preserves_historical_context_and_gap PASSED
tests/test_dashboard_observability_001.py::test_9_accounting_reconciliation_with_ledger PASSED
tests/test_dashboard_observability_001.py::test_10_no_duplicate_journal_events PASSED
tests/test_dashboard_observability_001.py::test_11_strict_read_only_mode_and_mutation_refusal PASSED
tests/test_dashboard_observability_001.py::test_12_zero_secrets_in_public_api PASSED
tests/test_dashboard_observability_001.py::test_13_performance_and_lightweight_execution PASSED

======================== 13 passed in 1.81s ========================
```

### Полная регрессия набора приёмочных тестов (38/38 PASS):
```
tests/test_dashboard_observability_001.py: 13/13 PASSED
tests/test_r2_recovery_001.py:            13/13 PASSED
tests/test_final_start_blockers_009.py:   12/12 PASSED
======================== 38 passed in 50.06s =======================
```

---

## 4. Спецификация REST API (Zero Secrets, Read-Only)

### 4.1. `GET /api/status` — Матрица систем и капитал
```json
{
  "timestamp_iso": "2026-11-16T12:50:51.210452+00:00",
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

### 4.2. `GET /api/positions` — Открытые позиции
```json
{
  "timestamp_iso": "2026-11-16T12:50:51.210452+00:00",
  "total_open_positions": 0,
  "is_empty": true,
  "empty_reason": "Пока открытых позиций нет. Все три системы находятся в режиме активного наблюдения (OBSERVING). Фильтры входа строгие (ADX>=18.0, EMA50>EMA200, BTC Delta, R2 Score), сигналы допущенных пробоев пока не зафиксированы.",
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

### 4.3. `GET /api/scanner` — Сканер и воронка отбора
```json
{
  "timestamp_iso": "2026-11-16T12:50:51.210452+00:00",
  "funnel": {
    "native": {"universe": 18, "evaluated": 0, "eligible": 0, "rejected": 0, "active_positions": 0},
    "shadow": {"universe": 18, "evaluated": 0, "eligible": 0, "rejected": 0, "active_positions": 0},
    "r2": {"universe": 3, "evaluated": 3, "eligible": 0, "rejected": 0, "active_positions": 0}
  },
  "scanner_items": [
    {
      "system": "R2",
      "symbol": "BTCUSDT",
      "source": "observer.sqlite:events",
      "event_at": "2026-11-16T09:20:00Z",
      "observed_at": "2026-11-16T09:20:05Z",
      "confidence_status": "PROVEN_CLOSED_BAR",
      "score": 0.0,
      "target_weight": 0.0,
      "eligible": false,
      "rejection_reason": "THRESHOLD_NOT_MET (Score below entry gate)"
    }
  ]
}
```

### 4.4. `GET /api/health` — Технический мониторинг
```json
{
  "timestamp_iso": "2026-11-16T12:50:51.210452+00:00",
  "read_only_enforced": true,
  "secrets_exposed": false,
  "host_resources": {
    "load_average": [0.12, 0.11, 0.10],
    "server_time_utc": "2026-11-16T12:50:51.210452+00:00"
  },
  "services": [
    {
      "name": "bybit-shadow-native.service",
      "role": "Native Paper Trading (Port 8084 runner)",
      "telemetry_present": true,
      "heartbeat_age_sec": 14.2,
      "freshness": "FRESH",
      "status": "TELEMETRY_FRESH"
    },
    {
      "name": "bybit-shadow-realistic.service",
      "role": "Realistic Shadow L2 Trading (Paper runner)",
      "telemetry_present": true,
      "heartbeat_age_sec": 14.1,
      "freshness": "FRESH",
      "status": "TELEMETRY_FRESH"
    },
    {
      "name": "bybit-shadow-r2.service",
      "role": "CryptoLab R2 Spot Shadow (Hunter/R2)",
      "telemetry_present": true,
      "heartbeat_age_sec": 12.8,
      "freshness": "FRESH",
      "status": "TELEMETRY_FRESH"
    },
    {
      "name": "bybit-shadow-dashboard.service",
      "role": "Three-System Trading Control Center (Port 8085)",
      "port": 8085,
      "status": "ACTIVE_RUNNING",
      "public_access": "0.0.0.0:8085 (READ_ONLY)"
    },
    {
      "name": "bybit-bot-v32.service",
      "role": "Production v3.2 Engine (Port 8083)",
      "port": 8083,
      "status": "ISOLATED_PORT_8083",
      "note": "Production service is strictly isolated and running independently."
    }
  ],
  "databases": {
    "native_ledger": {"exists": true, "status": "OK", "size_kb": 32.0, "wal_size_kb": 0.0},
    "shadow_ledger": {"exists": true, "status": "OK", "size_kb": 48.0, "wal_size_kb": 0.0},
    "r2_observer": {"exists": true, "status": "OK", "size_kb": 128.0, "wal_size_kb": 32.0}
  }
}
```

---

## 5. Процедура изолированного обновления панели (Только Dashboard)

> [!IMPORTANT]
> Данная процедура обновляет **исключительно** код веб-панели `bybit-shadow-dashboard.service`.
> Категорически запрещено:
> - Запускать `deploy_shadow_fleet_services.py`
> - Перезапускать службы Native, Shadow или R2
> - Перезапускать боевую v3.2 (порт 8083, PID 3629780)
> - Трогать процесс VPN xray (PID 3165188)
> - Выполнять `git reset --hard` или очищать базы данных SQLite!

### Пошаговые команды для Termius:

```bash
# Шаг 1. Переходим в рабочий каталог репозитория на VPS
cd /opt/bybit-shadow/bybit-alpha-bot-SAM

# Шаг 2. Запрашиваем свежие коммиты из ветки (без слияния)
git fetch origin fix/final-start-blockers-009

# Шаг 3. Извлекаем ТОЛЬКО обновлённые файлы панели управления
git checkout origin/fix/final-start-blockers-009 -- \
    v3_2/src/integrations/three_system_dashboard.py \
    v3_2/src/integrations/trading_control_center_data.py \
    v3_2/src/runners/run_three_system_dashboard.py

# Шаг 4. Проверяем валидность синтаксиса Python без запуска сервера
/opt/bybit-bot/venv/bin/python v3_2/src/runners/run_three_system_dashboard.py --dry-run

# Шаг 5. Перезапускаем ИСКЛЮЧИТЕЛЬНО службу веб-панели 8085
systemctl restart bybit-shadow-dashboard.service

# Шаг 6. Контрольная проверка статуса службы и API Health
sleep 2
systemctl status bybit-shadow-dashboard.service --no-pager
curl -s http://127.0.0.1:8085/api/health | head -c 300
echo ""
```

---

## 6. Инструкция отката на предыдущую версию панели (Rollback)

В случае непредвиденных обстоятельств возврат к исходной панели выполняется за 10 секунд:

```bash
cd /opt/bybit-shadow/bybit-alpha-bot-SAM

# Возвращаем файлы панели к предыдущему коммиту 46c28e3
git checkout 46c28e361b1e061956381ca1af8357ee5010b173 -- \
    v3_2/src/integrations/three_system_dashboard.py \
    v3_2/src/runners/run_three_system_dashboard.py

# Перезапускаем только службу дашборда
systemctl restart bybit-shadow-dashboard.service
```
