# DASHBOARD_OBSERVABILITY_001_DATA_MAP.md
# Аудит источников данных TORG_BOT Trading Control Center v1.0 (Release: TRADING_CONTROL_CENTER_V1_FINAL)

**Протокол:** `DASHBOARD-OBSERVABILITY-001`  
**Ревизия:** `TRADING_CONTROL_CENTER_V1_FINAL (Audit & Verification Complete)`  
**Дата актуализации:** 2026-11-16  
**Режим:** 100% Read-Only Observer, zero production modification, real SQLite schemas, Dual UTC/MSK time format.

---

## 1. Архитектурные принципы и фундаментальные инварианты

Панель **Trading Control Center v1.0 (Порт 8085)** спроектирована как полностью независимый наблюдатель (Observer), агрегирующий состояние трёх автономных исследовательских систем:
1. **System A — Native Dual-Regime** (Bybit Linear Perpetual, тренд Donchian 4h + Cascade TP 1.5R/3.0R + Range Mean-Reversion 1h)
2. **System B — Realistic Shadow** (Bybit Linear Perpetual, дискретные REST L2-снимки стакана + 8h funding)
3. **System C — CryptoLab R2-SHADOW** (Bybit Spot Cash, 24 внутренних профиля, основной эталон: `R2:300:BASE:OBSERVED_BOOK`)

### Незыблемые количественные инварианты (P0):
- **Использование исключительно настоящих SQLite-схем**: Запрещены любые запросы к вымышленным таблицам (`native_positions`, `shadow_positions`, `shadow_journal`, `shadow_trades`). Источником истины для состояния и открытых позиций являются реальные таблицы:
  - Native: `native_state_journal`, `native_journal`, `native_trades`, `native_missed_opportunities`, `native_funding_settlements`.
  - Shadow: `shadow_state_journal`, `shadow_dedup_events`, `shadow_execution_events`, `shadow_funding_settlements`.
  - CryptoLab R2: `observer.sqlite` (`accounts`, `events`).
- **Разделение ошибки источника и пустого портфеля**: При сбое базы данных, ошибке синтаксиса или повреждении файла возвращается статус `DATA_SOURCE_ERROR` с безопасной диагностикой. Статус `OK_EMPTY` («Позиций нет») выводится только при подтверждённом пустом состоянии. Повреждённый журнал никогда не превращается в пустой портфель.
- **Достоверность открытых позиций (P0.1, P0.2)**:
  - `current_mark`: подтверждённая рыночная цена из телеметрии/состояния либо `NOT_AVAILABLE`. Запрещена подмена цены входа ценой маркировки.
  - `unrealized_pnl_usdt`: вычисляется строго по обоснованному количественному контракту: `direction * (current_mark - entry_price) * qty` при наличии подтверждённой цены маркировки, либо `NOT_AVAILABLE`. Запрещён вывод фиктивного нуля.
  - `last_sl_update_utc`: выводится только при наличии зарегистрированного события изменения SL, иначе `NOT_RECORDED`. Запрещена подмена времени входа временем обновления стопа.
  - Native Cascade TP: в действительном `CoreV3Engine.to_dict()` отсутствуют `tp1_taken` и `tp2_taken`. Расчётные уровни TP (1.5R, 3.0R) отделены от статуса исполнения. Для неизвестного состояния частичного выхода используется `UNKNOWN`, запрещены неподтверждённые флаги `False` или `PENDING`.
- **Точные соответствия полей закрытых сделок Shadow (P0.3)**:
  - Схема соответствует действительному `TradeRecord.to_dict()`: `initial_qty`, `final_qty`, `funding_pnl`, `fees_paid`, `gross_pnl`, `net_pnl`, `exit_reason`, `slippage_bps`.
  - Запрещена подмена отсутствующих финансовых величин нулями (используется `NOT_RECORDED`).
- **Честный учёт капитала и графики (P0.4)**:
  - Native: кривая капитала читается из `total_equity`, выборка возвращает последние 500 записей в прямом хронологическом порядке.
  - Shadow: полная MTM Equity рассчитывается как `shadow_realized_equity + unrealized_pnl` по открытым позициям; запрещено выдавать реализованный капитал за полный MTM. Выборка последних 500 записей.
  - R2: прямая SQL-фильтрация последних 500 записей `SNAPSHOT` для профиля `R2:300:BASE:OBSERVED_BOOK` (`strategy='R2' AND capital=300 AND cost='BASE' AND policy='OBSERVED_BOOK'`). Паритет ключей `r2` и `r2_shadow`.
  - Разрыв наблюдений R2 (01:03–09:20 UTC) сохраняется на SVG-графиках в виде разорванных сегментов без соединения точек через окно отсутствия данных.
- **Бухгалтерская верификация и конечный автомат (P0.5)**:
  - Запрещено объявлять `ACCOUNTING_VERIFIED=OK` при отсутствии подтверждённых сделок. До появления операций выводится статус `ACCOUNTING_NOT_YET_OBSERVED`.
  - При наличии сделок сверяется балансовый инвариант: `initial_capital + sum(net_pnl) + sum(funding_pnl) == current_realized_equity`.
  - Статусы: `ACCOUNTING_NOT_YET_OBSERVED` -> `ACCOUNTING_PENDING_RECONCILIATION` -> `ACCOUNTING_VERIFIED` или `ACCOUNTING_FAILED`.
  - Комиссии соответствуют тарифу VIP0 (0.100% taker, 0.036% maker).
- **Только подтверждённые факты в сканере (P0.6)**:
  - Количество обработанных свечей после T0 читается из `native_journal` (`closed_bar_%`) для Native и `shadow_dedup_events` для Shadow (в текущем прогоне подтверждено по 12 баров на монету = 216 баров).
  - Закрытый бар без зарегистрированного сигнала помечается `market_regime="NOT_RECORDED"`, `decision="NO_DECISION_RECORDED"`, `confidence_status="BAR_PROCESSED / NO_SIGNAL_RECORDED"`. Запрещены вымышленные режимы `NO_BREAKOUT` или решения `OBSERVING_MARKET`.
  - Для CryptoLab R2 отображаются 3 спотовые монеты с пояснением «Суточный квантовый ребаланс (Daily 00:00 UTC)» и бейджем `FORWARD_VIRTUAL_ESTIMATE`.
- **Двойной формат времени (UTC / MSK)**: Все временные метки событий, закрытых баров, сделок и позиций форматируются в виде `YYYY-MM-DD HH:MM:SS UTC (HH:MM:SS MSK)`.

---

## 2. Карта реальных путей на сервере VPS (`150.241.98.70`)

| Назначение | Реальный путь на VPS | Локальный fallback (Dev/Test) |
|---|---|---|
| **Native Ledger DB** | `/opt/bybit-shadow/data/native_ledger.db` | `reports/v32_native_ledger.db` |
| **Shadow Ledger DB** | `/opt/bybit-shadow/data/shadow_ledger.db` | `reports/v32_shadow_ledger.db` |
| **CryptoLab R2 DB** | `/opt/bybit-shadow/data/cryptolab/observer.sqlite` | `data/cryptolab/observer.sqlite` |
| **Native Telemetry** | `/opt/bybit-shadow/telemetry/telemetry_native.json` | `reports/telemetry_native.json` |
| **Shadow Telemetry** | `/opt/bybit-shadow/telemetry/telemetry_shadow.json` | `reports/telemetry_shadow.json` |
| **R2 Telemetry** | `/opt/bybit-shadow/data/cryptolab/telemetry.json` | `data/cryptolab/telemetry.json` |
| **Native State JSON** | `/opt/bybit-shadow/data/native_state.json` | `reports/v32_native_state.json` |
| **Shadow State JSON** | `/opt/bybit-shadow/data/shadow_state.json` | `reports/v32_shadow_state.json` |

---

## 3. Детальная карта источников данных по 6 экранам

### 3.1. Вкладка 1: Общая сводка (Overview Cards & Key Metrics)
- **Native Equity**: Читается из `native_journal` (`total_equity`), fallback на `native_state.json`.
- **Shadow Equity (MTM)**: `shadow_realized_equity + unrealized_pnl` из `shadow_state_journal`.
- **CryptoLab R2 Equity**: Прямой SQL запрос к `observer.sqlite` (`accounts` где `strategy='R2' AND capital=300 AND cost='BASE' AND policy='OBSERVED_BOOK'`), fallback на `telemetry.json`.
- **Cash & Notional**: Читается из состояний и балансовых таблиц.
- **Статус финансовой сверки**: Конечный автомат `compute_accounting_reconciliation()`.

### 3.2. Вкладка 2: Открытые позиции (Open Positions Matrix)
- **Статус источника**: `OK_POSITIONS` (если есть позиции), `OK_EMPTY` (подтверждённый пустой портфель), `DATA_SOURCE_ERROR` (сбой БД).
- **Цены и PnL**: `current_mark` из маркировки (или `NOT_AVAILABLE`). `unrealized_pnl` строго по формуле или `NOT_AVAILABLE`.
- **Статусы SL / TP**: `last_sl_update_utc` только по фактическому событию. TP1/TP2: `TAKEN`, `PENDING` или `UNKNOWN`.

### 3.3. Вкладка 3: Сканер рынка (Dynamic Universe Scanner)
- **System A (Native, 18 монет)**: Свечи считаются по `native_journal` (`closed_bar_%`).
- **System B (Shadow, 18 монет)**: Свечи считаются по `shadow_dedup_events`.
- **System C (CryptoLab R2, 3 монеты)**: Ребаланс Daily 00:00 UTC, бейдж `FORWARD_VIRTUAL_ESTIMATE`.

### 3.4. Вкладка 4: Журнал торговых событий (Trade Execution Journal)
- Фильтрация служебных событий: `MANUAL_SAVE` исключены.
- Подлинные события: `closed_bar_4h`, `SIGNAL_REJECTED`, `ENTRY_MARKET`, `EXIT_MARKET`, `TRAILING_STOP`, `CASCADE_TP1`, `FUNDING_SETTLEMENT`.

### 3.5. Вкладка 5: Системный мониторинг и здоровье (System Health)
- Статусы 4 служб флота (`bybit-shadow-native`, `bybit-shadow-realistic`, `bybit-shadow-r2`, `bybit-shadow-dashboard`).
- Таблица известных инцидентов (`Known Incidents`):
  1. `R2 Data Gap` (01:03–09:20 UTC) — `RESOLVED (Fixed in 46c28e3, T0 & cohort preserved)`.
  2. `R2 Resource Limits Breach` (10:47:35 UTC) — `MONITORING (TRIGGER_NOT_IDENTIFIED, restarts=1799)`.
- Таблица неприкосновенности защищённой инфраструктуры (порт 8083, Xray VPN PID 3165188).

### 3.6. Вкладка 6: Форензик и бухгалтерские инварианты (Forensic Audit)
- Полная проверка 11 количественных инвариантов.
- Сверка сохранения T0 (`2026-11-16T00:00:00Z`) и когорты (`20261116_000000_THREE_SYSTEM_SHADOW`).
