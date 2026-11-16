# DASHBOARD_OBSERVABILITY_001_DATA_MAP.md
# Аудит источников данных TORG_BOT Trading Control Center v1.0
**Протокол:** `DASHBOARD-OBSERVABILITY-001`  
**Дата:** 2026-11-16  
**Режим:** 100% Read-Only, zero production modification, no runner changes in Phase 1

---

## 1. Введение и архитектурные принципы

Панель **Trading Control Center v1.0 (Порт 8085)** спроектирована как полностью независимый наблюдатель (Observer), агрегирующий состояние трёх автономных исследовательских систем:
1. **System A — Native Dual-Regime** (Bybit Linear Perpetual, тренд Donchian 4h + Range Mean-Reversion 1h)
2. **System B — Realistic Shadow** (Bybit Linear Perpetual, дискретные REST L2-снимки стакана + 8h funding)
3. **System C — CryptoLab R2-SHADOW** (Bybit Spot Cash, 24 внутренних профиля, 3 экспортируемых эталона)

### Незыблемые инварианты (P0):
- **Только подтверждённые факты**: При отсутствии данных выводится `NOT_AVAILABLE`, `NOT_RECORDED`, `INSUFFICIENT_SAMPLE` или `NOT_DEFINED`. Никакой вымышленной генерации сделок, сигналов или целевых дат.
- **Физическая изоляция баз данных**: Все подключения к SQLite выполняются строго в режиме `mode=ro` (`uri=True`, read-only). Создание таблиц, индексов, триггеров или временных таблиц в рабочих базах категорически запрещено.
- **Защита ресурсов VPS (1 vCPU, 2GB RAM)**: Запросы к SQLite кэшируются на 5–10 секунд, с лимитами `LIMIT 50`–`LIMIT 200` и проекцией только необходимых колонок. Запрещены полные сканирования WAL-баз.
- **Zero Secrets**: Никакие переменные окружения, ключи API, пути к `.env` или `user_authorization.json` не отдаются через HTTP.

---

## 2. Карта источников данных по экранам

### 2.1. Главный экран — Общая сводка (Overview Cards)

| Поле UI | Система | Точный источник | Таблица / Ключ JSON | Частота обновления | Метод чтения | Риск устаревания | Возможное null | Требует изменения раннера? |
|---|---|---|---|---|---|---|---|---|
| **Realized Equity** | Native / Shadow / R2 | `reports/telemetry_native.json`, `reports/telemetry_shadow.json`, `data/cryptolab/telemetry.json` | `equity_usdt` / `valuation.total_equity` | ~10-15 сек | JSON parse | Низкий (при сбое демона статус -> STALE) | Да (до первой оценки) | Нет |
| **Cash Balance** | Native / Shadow / R2 | Те же файлы телеметрии | `cash_usdt` | ~10-15 сек | JSON parse | Низкий | Да | Нет |
| **Net PnL & Return %** | Native / Shadow / R2 | Те же файлы телеметрии | `net_pnl_usdt`, `net_return_pct` | ~10-15 сек | JSON parse | Низкий | Да | Нет |
| **Max Drawdown %** | Native / Shadow / R2 | Те же файлы телеметрии | `mdd_pct` | ~10-15 сек | JSON parse | Низкий | Да | Нет |
| **Current Exposure %** | Native / Shadow / R2 | Те же файлы телеметрии | `exposure_pct` | ~10-15 сек | JSON parse | Низкий | Да | Нет |
| **Turnover USDT** | Native / Shadow / R2 | Те же файлы телеметрии | `turnover_usdt` | ~10-15 сек | JSON parse | Низкий | Да | Нет |
| **Fees Paid USDT** | Native / Shadow / R2 | Те же файлы телеметрии | `fees_paid_usdt` / `totals.fees_usdt` | ~10-15 сек | JSON parse | Низкий | Да | Нет |
| **Slippage / Spread Cost** | Native / Shadow / R2 | Те же файлы телеметрии | `slippage_cost_usdt` / `totals.spread_impact_usdt` | ~10-15 сек | JSON parse | Низкий | Да | Нет |
| **Funding Paid USDT** | Native / Shadow (Perp) | `reports/telemetry_native.json`, `reports/telemetry_shadow.json` | `funding_paid_usdt` | ~10-15 сек | JSON parse | Низкий | Нет (0.0 для Spot R2) | Нет |
| **Active Positions Count** | Native / Shadow / R2 | Те же файлы телеметрии | `positions_count` | ~10-15 сек | JSON parse | Низкий | Нет (0) | Нет |
| **Closed Trades Count** | Native / Shadow | `reports/v32_native_ledger.db`, `reports/v32_shadow_ledger.db` | `SELECT count(*) FROM {native,shadow}_trades` | По запросу / кэш 10с | SQLite `mode=ro` | Низкий | Нет (0) | Нет |
| **Last Decision Time** | Native / Shadow / R2 | `v32_{native,shadow}_state.json`, `data/cryptolab/telemetry.json` | `last_processed_bar_ts` / `last_signal_at` | На закрытии бара / 15с | JSON parse | Средний (при низкой волатильности) | Да | Нет |
| **Last Market Mark Time** | Native / Shadow / R2 | Те же файлы телеметрии | `timestamp_utc` / `market.at` | ~10-15 сек | JSON parse | Низкий | Да | Нет |
| **Heartbeat Age (sec)** | Native / Shadow / R2 | Те же файлы телеметрии | `now_utc - timestamp_utc` | Каждые 10 сек | Расчёт в памяти | Показывает факт остановки | Нет | Нет |
| **Status Badges** | Native / Shadow / R2 | Комбинация полей телеметрии | `status`, `data_freshness_status`, `valuation_status` | Каждые 10 сек | Логический маппинг | Нет | Нет | Нет |

---

### 2.2. Вкладка «Сканер / Отбор монет» (Scanner)

| Поле UI | Система | Точный источник | Таблица / Ключ JSON | Частота обновления | Метод чтения | Риск устаревания | Возможное null | Требует изменения раннера? |
|---|---|---|---|---|---|---|---|---|
| **Вселенная монет (Symbol)** | Native / Shadow | Конфигурация движка (`DEFAULT_SYMBOLS`) + `last_processed_bar_ts` | 18 символов (`BTCUSDT`, `ETHUSDT`, `SOLUSDT` и др.) | Статично / на каждом баре | Память / `state.json` | Нулевой | Нет | Нет |
| **Вселенная монет (Symbol)** | CryptoLab R2 | `observer.sqlite` / `telemetry.json` | `market.symbols` (`BTCUSDT`, `ETHUSDT`, `SOLUSDT`) | ~15 сек | JSON / SQLite | Нулевой | Нет | Нет |
| **Market Regime** | Native / Shadow | `reports/v32_native_state.json` (`range_engine` vs `engine`) | Эвристика по активному движку и `native_missed_opportunities` | При закрытии бара | JSON / SQLite `mode=ro` | Низкий | Да (`UNCONFIRMED`) | Нет (для подробного breakdown по барам — Этап 2) |
| **Last Closed Bar** | Native / Shadow | `reports/v32_native_state.json` | `last_processed_bar_ts[symbol]` | При закрытии бара | JSON parse | Низкий | Да (`NOT_PROCESSED_YET`) | Нет |
| **Signal (LONG/SHORT/HOLD/NONE)** | Native / Shadow | `native_positions`, `native_missed_opportunities` | `direction` (если есть позиция или отклоненный сигнал) | При событии | SQLite `mode=ro` | Низкий | Да (`NONE`) | Нет |
| **Signal & Target Weights** | CryptoLab R2 | `data/cryptolab/telemetry.json`, `observer.sqlite` | `signal`, `target_weights`, `events WHERE kind='DECISION'` | Каждые ~15 сек | JSON / SQLite `mode=ro` | Низкий | Да | Нет |
| **Score / R2 Score** | CryptoLab R2 | `observer.sqlite` | `events WHERE kind='SNAPSHOT'` -> `r2_score` | Каждые ~15 сек | SQLite `mode=ro` | Низкий | Да (`NOT_AVAILABLE`) | Нет |
| **Eligible (YES/NO)** | CryptoLab R2 | `observer.sqlite` | `events WHERE kind='SNAPSHOT'` -> `eligible` | Каждые ~15 сек | SQLite `mode=ro` | Низкий | Да | Нет |
| **Причина отказа (Filter/Reason)** | Native | `v32_native_ledger.db` | `native_missed_opportunities.reason` | При отклонении | SQLite `mode=ro` | Низкий | Да (`NONE`) | Нет |
| **Воронка решений (Funnel Counts)** | Native / Shadow / R2 | `DEFAULT_SYMBOLS`, `state.json`, `positions`, `missed_opportunities` | Вычисление: Universe -> Evaluated -> Filtered -> Filled | Каждые 10 сек | Агрегация read-only | Низкий | Нет | Нет |

---

### 2.3. Вкладка «Открытые позиции» (Open Positions)

| Поле UI | Система | Точный источник | Таблица / Ключ JSON | Частота обновления | Метод чтения | Риск устаревания | Возможное null | Требует изменения раннера? |
|---|---|---|---|---|---|---|---|---|
| **Symbol & Side** | Native / Shadow | `v32_{native,shadow}_ledger.db` -> `{native,shadow}_positions`, `state.json` | `symbol`, `direction` (+1: LONG, -1: SHORT) | Реалтайм (на тике/баре) | SQLite `mode=ro` + JSON | Низкий | Нет | Нет |
| **Side (R2)** | CryptoLab R2 | `data/cryptolab/telemetry.json` -> `quantities` | `BTCUSDT`, `ETHUSDT`, `SOLUSDT` (только Spot Long) | ~15 сек | JSON parse | Низкий | Нет | Нет |
| **Open UTC Time** | Native / Shadow | `native_positions.entry_time_ms`, `PositionState.entry_time_ms` | `entry_time_ms` -> ISO UTC | При входе | SQLite `mode=ro` + JSON | Низкий | Да (если нет позиций) | Нет |
| **Entry Price & Qty** | Native / Shadow | `native_positions`, `shadow_positions` | `entry_price`, `current_qty` | При входе / частичном закрытии | SQLite `mode=ro` | Низкий | Да | Нет |
| **Notional USDT** | Native / Shadow | Вычисляется | `current_qty * current_price` | Каждые 10 сек | Расчёт в памяти | Низкий | Да | Нет |
| **Current Stop Loss** | Native / Shadow | `native_positions.stop_loss`, `PositionState.current_stop` | `stop_loss` / `current_stop` | На каждом баре/тике | SQLite `mode=ro` + JSON | Низкий | Да (`NOT_DEFINED`) | Нет |
| **Take Profit 1 & 2** | Shadow / Native | `PositionState.tp1_price`, `PositionState.tp2_price`, `RangeActivePosition.tp1_price` | `tp1_price`, `tp2_price` | При расчёте входа | JSON state `mode=ro` | Низкий | Да (`NOT_DEFINED` для Trend Native) | Нет |
| **TP Flags** | Shadow / Native | `PositionState.tp1_taken`, `PositionState.tp2_taken`, `is_breakeven_set` | Boolean флаги | На каждом тике | JSON state `mode=ro` | Низкий | Нет (False) | Нет |
| **Bars Held** | Native / Shadow | `PositionState.bars_held`, `ActivePosition.bars_held` | `bars_held` | На закрытии бара | JSON state `mode=ro` | Низкий | Нет (0) | Нет |
| **Unrealized PnL** | Native / Shadow | `native_positions.unrealized_pnl`, `shadow_positions.unrealized_pnl` | `unrealized_pnl` | На тике/марке | SQLite `mode=ro` | Низкий | Да | Нет |
| **Entry Fees & Funding** | Shadow / Native | `PositionState.entry_fees`, `PositionState.funding_pnl` | `entry_fees`, `funding_pnl` | При входе / начислении | JSON state `mode=ro` | Низкий | Да | Нет |

---

### 2.4. Вкладка «Журнал торговых событий» (Trade Journal)

| Поле UI | Система | Точный источник | Таблица / Ключ JSON | Частота обновления | Метод чтения | Риск устаревания | Возможное null | Требует изменения раннера? |
|---|---|---|---|---|---|---|---|---|
| **Исполнения ордеров (Fills)** | Shadow | `v32_shadow_ledger.db` | `shadow_execution_events` | При событии | SQLite `mode=ro` | Низкий | Да | Нет |
| **Отклонённые сигналы** | Native | `v32_native_ledger.db` | `native_missed_opportunities` | При отклонении | SQLite `mode=ro` | Низкий | Да | Нет |
| **Снимки состояния** | Native / Shadow | `native_state_journal`, `shadow_state_journal` | `saved_at_utc`, `event_key`, `realized_equity` | При смене состояния | SQLite `mode=ro` | Низкий | Да | Нет |
| **Начисления funding** | Native / Shadow | `native_funding_settlements`, `shadow_funding_settlements` | `settlement_time_ms`, `funding_rate`, `payment` | Каждые 8 часов | SQLite `mode=ro` | Низкий | Да | Нет |
| **Закрытые сделки** | Native / Shadow | `native_trades`, `shadow_trades` | `trade_id`, `net_pnl`, `exit_reason` | При закрытии сделки | SQLite `mode=ro` | Низкий | Да | Нет |
| **События CryptoLab R2** | CryptoLab R2 | `data/cryptolab/observer.sqlite` | `events` (`ORDER`, `DECISION`, `EQUITY`, `SNAPSHOT`) | ~15 сек | SQLite `mode=ro` | Низкий | Да | Нет |

---

### 2.5. Вкладка «История торговли и аналитика» (Analytics)

| Поле UI | Система | Точный источник | Таблица / Ключ JSON | Частота обновления | Метод чтения | Риск устаревания | Возможное null | Требует изменения раннера? |
|---|---|---|---|---|---|---|---|---|
| **История Equity (Временной ряд)** | Native / Shadow | `native_journal`, `shadow_journal` | `iso_time`, `realized_equity` | На каждом событии/баре | SQLite `mode=ro` | Низкий | Да | Нет |
| **История Equity (R2)** | CryptoLab R2 | `observer.sqlite` -> `events WHERE kind='EQUITY'` | `at`, `payload.mtm` | ~15 сек | SQLite `mode=ro` | Низкий | Да | Нет |
| **Закрытые сделки (Таблица)** | Native / Shadow | `native_trades`, `shadow_trades` | Полная таблица закрытых сделок | При выходе из позиции | SQLite `mode=ro` | Низкий | Да (пусто до закрытия) | Нет |
| **Издержки (Fees, Slippage, Funding)** | Native / Shadow / R2 | `telemetry_*.json`, `shadow_execution_events` | `fees_paid_usdt`, `slippage_cost_usdt`, `funding_paid_usdt` | ~10-15 сек | JSON / SQLite | Низкий | Нет (0.0) | Нет |
| **Разрыв данных R2 (Data Gap)** | CryptoLab R2 | Системный аудит VPS (инцидент 01:03–09:20 UTC) | Фиксированный диапазон `2026-11-16 01:03:32Z — 2026-11-16 09:20:00Z` | Статический маркер | Память | Нулевой | Нет | Нет |

---

### 2.6. Вкладка «Технический мониторинг» (System Health)

| Поле UI | Источник | Таблица / Команда | Частота | Метод | Риск безопасности |
|---|---|---|---|---|---|
| **Статусы служб systemd** | Изолированная read-only проверка PID и telemetry heartbeat | Проверка возраста файлов телеметрии и PID в `/proc` / telemetry | Каждые 10 сек | Без выполнения shell-команд | Полная безопасность (0% риска) |
| **Целостность баз данных** | 3 файла SQLite | `PRAGMA quick_check(1)` | Каждые 60 сек | SQLite `mode=ro` | Нулевой (read-only) |
| **Размеры баз и WAL** | Файловая система | `os.path.getsize(path)` | Каждые 30 сек | Python `os.stat` | Нулевой |
| **Нагрузка сервера** | `/proc/loadavg` (Linux) / `os.getloadavg()` | Load average 1m, 5m, 15m | Каждые 10 сек | Python API | Нулевой |

---

## 3. Перечень пробелов данных (Data Gaps & Future Enhancements)

Следующие поля **не могут быть получены достоверно** из текущих данных без изменения торговых раннеров и по требованию ТЗ честно помечаются в UI как `NOT_RECORDED`, `NOT_AVAILABLE` или `NOT_DEFINED`:

1. **Плановое время достижения TP или SL**:
   - *Статус*: Принципиально не существует в количественных моделях.
   - *Отображение в UI*: Строго `NOT_APPLICABLE` / фактическое время срабатывания после наступления события.
2. **TP1 и TP2 для Trend-стратегии Native**:
   - *Статус*: Оригинальная v3.0/v3.2 Donchian Trend использует динамический трейлинг-стоп и выход по противоположному пробою канала Donchian, а не фиксированные TP-уровни.
   - *Отображение в UI*: `NOT_DEFINED (Donchian Trailing / Channel Exit)`.
3. **Посекундные индикаторы стакана (BBO depth, micro-spread) для Native**:
   - *Статус*: Native исполняется по дискретным ценам баров (концепция Taker MNT). Посекундные L2-стаканы записывает только Shadow (`shadow_execution_events`).
   - *Отображение в UI*: Для Native стакан помечается как `DISCRETE_BAR_EXECUTION`.
4. **Реальные биржевые исполнения ордеров для R2**:
   - *Статус*: R2 работает в режиме Shadow Observer. Все записи имеют статус `QUOTE_ESTIMATE_NOT_PROVED_FILL`.
   - *Отображение в UI*: Строго маркируются бейджем `VIRTUAL_MODEL_ESTIMATE`, запрещено называть реальными сделками.
5. **Данные R2 за период 01:03:32Z — 09:20:00Z 16 ноября 2026**:
   - *Статус*: Период простоя из-за `READINESS_EXPIRED_OR_FUTURE`.
   - *Отображение в UI*: График Equity отображает явный визуальный разрыв `RESEARCH_DATA_GAP (Server Recovery Period)`.
