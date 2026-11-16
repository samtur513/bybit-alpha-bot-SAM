# DASHBOARD_OBSERVABILITY_001_DATA_MAP.md
# Аудит источников данных TORG_BOT Trading Control Center v1.0 (Audit Repair-003)
**Протокол:** `DASHBOARD-OBSERVABILITY-001`  
**Ревизия:** `REPAIR-003 AUDIT RESOLUTION`  
**Дата актуализации:** 2026-11-16  
**Режим:** 100% Read-Only Observer, zero production modification, real SQLite schemas

---

## 1. Введение и архитектурные принципы

Панель **Trading Control Center v1.0 (Порт 8085)** спроектирована как полностью независимый наблюдатель (Observer), агрегирующий состояние трёх автономных исследовательских систем:
1. **System A — Native Dual-Regime** (Bybit Linear Perpetual, тренд Donchian 4h + Cascade TP 1.5R/3.0R + Range Mean-Reversion 1h)
2. **System B — Realistic Shadow** (Bybit Linear Perpetual, дискретные REST L2-снимки стакана + 8h funding)
3. **System C — CryptoLab R2-SHADOW** (Bybit Spot Cash, 24 внутренних профиля, основной эталон: `R2:300:BASE:OBSERVED_BOOK`)

### Незыблемые инварианты (P0):
- **Использование исключительно настоящих SQLite-схем**: Запрещены запросы к вымышленным таблицам (`native_positions`, `shadow_positions`, `shadow_journal`, `shadow_trades`). Источником истины для состояния и открытых позиций являются реальные таблицы:
  - Native: `native_state_journal`, `native_journal`, `native_trades`, `native_missed_opportunities`, `native_funding_settlements`.
  - Shadow: `shadow_state_journal`, `shadow_execution_events`, `shadow_funding_settlements`.
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
  - R2: ограниченная выборка последних 500 записей `EQUITY` для профиля `R2:300:BASE:OBSERVED_BOOK`. Устранено несовпадение ключей `r2` и `r2_shadow`.
  - Разрыв наблюдений R2 (01:03–09:20 UTC) сохраняется на SVG-графиках в виде разорванных сегментов без соединения точек через окно отсутствия данных.
- **Бухгалтерская верификация (P0.5)**:
  - Запрещено объявлять `ACCOUNTING_VERIFIED=OK` при отсутствии подтверждённых сделок. До появления операций выводится статус `ACCOUNTING_NOT_YET_OBSERVED`.
  - Комиссии соответствуют тарифу VIP0 (0.100% taker, 0.036% maker). Исключены любые предположения о скидке MNT без доказательства её применения.
- **Только подтверждённые факты в сканере (P0.6)**:
  - Закрытый бар без зарегистрированного сигнала помечается `market_regime="NOT_RECORDED"`, `decision="NO_DECISION_RECORDED"`, `confidence_status="NO_SIGNAL_RECORDED"`. Запрещены вымышленные режимы `NO_BREAKOUT` или решения `OBSERVING_MARKET`.
  - В R2 не считаются автоматически оценёнными инструменты без моделей/решений (`evaluated` отражает только реально оценённые монеты).
- **Физическая изоляция баз данных**: Все подключения к SQLite выполняются строго в режиме `mode=ro` (`uri=True`, read-only). Запросы с методами POST, PUT, DELETE, PATCH отклоняются с HTTP 405.
- **Защита ресурсов VPS (1 vCPU, 2GB RAM)**: Запросы к SQLite кэшируются на 5.0 секунд, с лимитами `LIMIT 50`–`LIMIT 500`. Исключены полные сканирования WAL-баз и `PRAGMA quick_check` при HTTP-запросах.
- **Безопасность UI (XSS Protection & Zero Secrets)**: Все поля из БД, JSON, заголовков, когорт и телеметрии экранируются через `html.escape`. Абсолютные пути сервера маскируются (`[root]/`).

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

## 3. Карта источников данных по экранам

### 3.1. Главный экран — Общая сводка (Overview Cards)

| Поле UI | Система | Точный источник | Таблица / Ключ JSON | Частота обновления | Метод чтения | Риск устаревания | Возможное null |
|---|---|---|---|---|---|---|---|
| **Total Equity** | Native | `native_ledger.db` / `telemetry_native.json` | `native_journal.total_equity` (NOT realized) | ~10-15 сек | SQLite `mode=ro` / JSON | Низкий | Да (до первого бара) |
| **MTM Total Equity**| Shadow | `shadow_ledger.db` / `shadow_state_journal` | `shadow_realized_equity + unrealized_pnl` | ~10-15 сек | SQLite `mode=ro` / JSON | Низкий | Да |
| **Equity Spot** | R2 | `observer.sqlite` / `telemetry.json` | `accounts(R2:300:BASE:OBSERVED_BOOK)` -> `mtm` | ~15 сек | SQLite `mode=ro` / JSON | Низкий | Да |
| **Cash Balance** | Native / Shadow / R2 | Состояние / Телеметрия | `cash_balance` / `cash_usdt` | ~10-15 сек | SQLite / JSON | Низкий | Да |
| **Net PnL & Return %** | Все системы | Рассчитывается от стартового $300 | `(equity - 300)` / `((equity/300)-1)*100` | ~10-15 сек | Расчёт в памяти | Низкий | Да |
| **Max Drawdown %** | Все системы | Журналы / Состояние | `mdd_pct` / `max_drawdown_pct` | ~10-15 сек | SQLite / JSON | Низкий | Да |
| **Fees Paid USDT** | Все системы | Журналы / События | `native_trades.fees_paid`, `shadow_portfolio.closed_trades.fees_paid` | При сделке | SQLite `mode=ro` | Низкий | Да |
| **Funding Paid USDT**| Native / Shadow (Perp) | Журналы фандинга | `native_funding_settlements.payment`, `shadow_funding_settlements.payment` | Каждые 8 часов | SQLite `mode=ro` | Низкий | Нет (0.0 для Spot R2) |
| **Accounting Verified**| Все системы | Сверка сделок | `ACCOUNTING_NOT_YET_OBSERVED` при отсутствии сделок, `OK` при успешной сверке | При запросе | Агрегатор в памяти | Низкий | Нет |

---

### 3.2. Вкладка «Сканер / Отбор монет» (Scanner)

| Поле UI | Система | Точный источник | Таблица / Ключ JSON | Правило валидации | Отображение при отсутствии |
|---|---|---|---|---|---|
| **Символ (Symbol)** | Native / Shadow | 18 инструментов постоянного пула v3.2 | `DEFAULT_SYMBOLS_18` | Проверяется наличие `last_processed_bar_ts > 0` | Отображается |
| **Символ (Symbol)** | CryptoLab R2 | 3 монеты Spot пула | `R2_SYMBOLS_3` (`BTCUSDT`, `ETHUSDT`, `SOLUSDT`) | Фиксированный пул | Отображается |
| **Источник (Source)** | Все системы | Внутренний маппинг | `NATIVE_STATE`, `SHADOW_STATE`, `R2_SNAPSHOT`, `LEDGER` | Фиксирует реальное происхождение строки | `UNKNOWN_SOURCE` |
| **Время события (Event At)**| Все системы | Исходные данные события | `last_processed_bar_ts`, `exchange_ts`, `at` | Реальное UTC-время бара или события (НЕ now!) | `NOT_RECORDED` |
| **Сигнал (Signal)** | Native / Shadow | Открытая позиция или отклоненный сигнал | `POSITION_HELD_{LONG/SHORT}`, `SIGNAL_{LONG/SHORT}` | Привязано к реальному направлению (+1/-1) | `NO_SIGNAL_RECORDED` |
| **Режим рынка (Market Regime)**| Native / Shadow | Проверенное состояние | `IN_POSITION`, `FILTER_REJECTED` | При отсутствии сигнала строго `NOT_RECORDED` | `NOT_RECORDED` |
| **Target Weights** | CryptoLab R2 | `telemetry.json` (`target_weights`) | `target_weights` (массив `[w_btc, w_eth, w_sol]`) | Маппинг по индексам 0=BTC, 1=ETH, 2=SOL | `0.0%` |
| **Score / R2 Score** | CryptoLab R2 | `telemetry.json` (`signal[sym].score`) | `score` / `r2_score` | Реальный числовой скор модели | `NOT_AVAILABLE` |
| **Допуск (Eligible)** | CryptoLab R2 | `telemetry.json` (`signal[sym].eligible`) | `eligible` (boolean) | `YES` если True, `NO` если False | `NOT_RECORDED` |
| **Решение (Decision)** | Все системы | Подтверждённое решение | `ALLOCATED_SPOT_LONG`, `REJECTED`, `MANAGING_POSITION` | Без сигнала строго `NO_DECISION_RECORDED` | `NO_DECISION_RECORDED` |
| **Статус достоверности** | Все системы | Синтезируется | `RECORDED_POSITION`, `PROVEN_FILTER_REJECTION`, `NO_SIGNAL_RECORDED`, `NOT_EVALUATED` | Отражает уровень доказательности | `NOT_EVALUATED` |

---

### 3.3. Вкладка «Открытые позиции» (Open Positions)

| Поле UI | Система | Точный источник | Таблица / Ключ JSON | Специфика обработки |
|---|---|---|---|---|
| **Статус источника** | Все системы | Адаптер чтения | `OK_POSITIONS`, `OK_EMPTY`, `DATA_SOURCE_ERROR` | При ошибке БД выводится предупреждающий баннер с диагностикой |
| **Symbol & Side** | Native / Shadow | `state_json` в `state_journal` | `engine.open_positions`, `shadow_portfolio.positions` | Направление: `1 -> LONG`, `-1 -> SHORT`. Принудительный LONG исключён |
| **Вход (Entry UTC)** | Native / Shadow | `state_json` | `entry_time` / `entry_time_ms` | Преобразуется в ISO UTC строку из миллисекунд |
| **Цена входа и Qty** | Native / Shadow | `state_json` | `entry_price`, `current_qty` | Точные числовые значения из модели |
| **Текущая марка (Mark)**| Native / Shadow | `state_json` / телеметрия | `current_mark`, `mark_price`, `last_price` | Подтверждённая марка, иначе `NOT_AVAILABLE` (не подменяется входом) |
| **Unrealized PnL** | Native / Shadow | Расчёт или поле | `direction * (mark - entry) * qty` | Точный математический расчёт, иначе `NOT_AVAILABLE` (не подменяется нулём) |
| **Stop Loss** | Native / Shadow | `state_json` | `current_stop` / `stop_loss` | Динамический трейлинг-стоп из состояния |
| **Время изменения SL** | Native / Shadow | `state_json` | `last_sl_update_utc`, `sl_update_time` | Только подтверждённое время события, иначе `NOT_RECORDED` |
| **Cascade Take Profit** | Native Trend | Расчёт по конфигурации v3.2 | `tp1 = entry + dir * 1.5 * atr`, `tp2 = entry + dir * 3.0 * atr` | Статус исполнения: `UNKNOWN` при отсутствии полей `tp1_taken`/`tp2_taken` в `CoreV3Engine.to_dict()` |
| **Take Profit 1 & 2** | Shadow | `state_json` | `tp1_price`, `tp2_price`, `tp1_taken`, `tp2_taken` | Фиксированные уровни дискретного исполнения Shadow |
| **Инвентарь R2 (Spot)**| CryptoLab R2 | `accounts(R2:300:BASE:OBSERVED_BOOK)` | `cash`, `qty` (`[qBTC, qETH, qSOL]`) | Спотовый инвентарь; SL/TP помечаются `NOT_APPLICABLE (Spot Portfolio Allocation)`, бейдж `VIRTUAL_MODEL_ESTIMATE` |

---

### 3.4. Вкладка «Журнал торговых событий» (Trade Journal)

| Источник событий | Таблица SQLite | Колонки | События |
|---|---|---|---|
| **Native Rejections** | `native_missed_opportunities` | `id, symbol, timestamp_ms, strategy, direction, intended_price, reason` | `SIGNAL_REJECTED` |
| **Native Exits** | `native_trades` | `trade_id, symbol, direction, entry_time_ms, exit_time_ms, exit_price, qty, net_pnl, exit_reason` | `EXIT_FILL` |
| **Native Funding** | `native_funding_settlements` | `symbol, settlement_time_ms, funding_rate, payment` | `FUNDING_SETTLEMENT` |
| **Shadow Executions**| `shadow_execution_events` | `event_id, exchange_ts, receive_ts, trade_id, symbol, event_type, fill_price, qty, realized_pnl, execution_status, notes` | `ENTRY_MARKET`, `EXIT_MARKET`, `TRAILING_STOP`, `CASCADE_TP1` |
| **CryptoLab R2** | `observer.sqlite` (`events`) | `id, kind, at, payload` (фильтр `strategy='R2' AND capital=300`) | `ORDER_INTENT`, `SIGNAL_DETECTED` |

---

### 3.5. Вкладка «История торговли и аналитика» (Analytics)

| Поле UI | Система | Источник | Логика выборки |
|---|---|---|---|
| **Equity Curve Native** | Native | `native_journal` | Последние 500 записей в прямом хронологическом порядке: `SELECT ... FROM (SELECT ... ORDER BY journal_id DESC LIMIT 500) ORDER BY journal_id ASC;` (используется `total_equity`!) |
| **Equity Curve Shadow** | Shadow | `shadow_state_journal` | Последние 500 записей с расчётом полного MTM Equity: `shadow_realized_equity + unrealized_pnl` |
| **Equity Curve R2** | CryptoLab R2 | `observer.sqlite` (`events`) | Ограниченная выборка последних 500 записей `EQUITY` для профиля `R2:300:BASE:OBSERVED_BOOK`. Паритет ключей `r2` и `r2_shadow` |
| **Closed Trades Native**| Native | `native_trades` | Полная таблица закрытых сделок с расчётом Win Rate и Profit Factor |
| **Closed Trades Shadow**| Shadow | `shadow_portfolio.closed_trades` | Полное соответствие полям `TradeRecord`: `initial_qty`, `final_qty`, `funding_pnl`, `fees_paid`, `net_pnl`, `gross_pnl`, `slippage_bps` |
| **Data Gap R2** | CryptoLab R2 | Разрыв 01:03–09:20 UTC | SVG-график разрывает полилинию на сегменты при разрыве времени $> 3600$с с отображением визуального маркера GAP |

---

### 3.6. Вкладка «Технический мониторинг» (System Health)

- **Статус служб**: Вычисляется строго по возрасту heartbeat/файла (`TELEMETRY_FRESH` при `<= 120s`, `TELEMETRY_STALE` при `> 120s`, `SERVICE_STATE_UNKNOWN` при отсутствии). Отражает свежесть телеметрии, а не прямые вызовы `systemctl`.
- **Dashboard Service**: Статус `ACTIVE_RUNNING` подтверждён фактом обслуживания текущего HTTP-запроса (`INDEPENDENT_HTTP_LISTENER`).
- **Целостность баз данных**: Проверка существования файлов, размеров DB и WAL. Исключён `PRAGMA quick_check(1)` при HTTP-запросах для защиты ресурсов 1 vCPU / 2GB RAM.
- **Безопасность API**: HTTP методы POST, PUT, DELETE, PATCH возвращают `405 Method Not Allowed` с закрытием соединения. Все ошибки маскируют внутренние пути и стек исключений.
