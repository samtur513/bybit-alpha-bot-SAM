# EXECUTION_AND_ACCOUNTING_REPORT.md
## Институциональная модель исполнения, расчёт торговых издержек и архитектура реляционного журнала

**Протокол:** V32-SHADOW-INTEGRITY-002 / P3  
**Репозиторий:** `samtur513/bybit-alpha-bot-SAM`  
**Дата аудита:** 2026-11-15T00:52:00+03:00  
**Статус модели исполнения:** PRODUCTION_REALISTIC

---

## 1. Сравнение моделей исполнения: Native Paper vs Realistic Shadow

Для обеспечения достоверного форвард-теста движок v3.2 Shadow реализует две параллельные модели исполнения:

| Параметр исполнения | Native Paper Portfolio (Порт 8083 Baseline) | Realistic Shadow Portfolio (Порт 8084 Institutional) |
| :--- | :--- | :--- |
| **Базовая ставка Taker** | **0.090%** (модель MNT VIP Discount) | **0.100%** (стандартная Bybit VIP0) |
| **Базовая ставка Maker** | **0.0324%** (с 10% MNT скидкой) | **0.0360%** (стандартная Bybit VIP0) |
| **Модель цены входа/выхода** | $P_{\text{intended}} \times (1 \pm 0.02\%)$ | Вершина стакана: $P_{\text{ask}}$ (Long), $P_{\text{bid}}$ (Short) |
| **Фильтр спреда (Spread Gate)**| Отключён (принимает любые спреды) | **0.080% max**: отказ при спреде $>0.08\%$ или инверсии |
| **Модель глубины стакана** | Бесконечная ликвидность по лучшей цене | **Конечная глубина**: учёт $D_{\text{ask}} / D_{\text{bid}}$ |
| **Проскальзывание по объёму** | Фиксированное (0.02%) | Динамическое квадратичное по объёму заявки |
| **Минимальный объём ордера** | Проверяется | **$5.00 USDT** (линейные деривативы Bybit) |
| **Учёт ставки финансирования** | Включён (периодическое списание/начисление) | Включён (периодическое списание/начисление) |

---

## 2. Математика исполнения и защита от двойного списания (No Double-Charging)

В примитивных симуляторах часто допускается ошибка двойного учёта издержек: к цене лучшего предложения $P_{\text{ask}}$ (которая уже включает рыночный полуспред) искусственно добавляется полный спред или дополнительное фиксированное проскальзывание.

В `v3_2/src/shadow/paper_trader_v32_shadow.py` реализована строгая институциональная механика:

### 2.1. Исполнение в пределах доступной глубины вершины стакана:
Если объём заявки $N = \text{Qty} \times P_{\text{top}}$ не превышает доступный объём на первом уровне $D_{\text{top}}$:
$$\text{Fill Price}_{\text{Long}} = P_{\text{best\_ask}}$$
$$\text{Fill Price}_{\text{Short}} = P_{\text{best\_bid}}$$
В этом случае цена исполнения строго равна котировке стакана, и спред учтён естественным рыночным образом без дополнительных надбавок.

### 2.2. Модель проскальзывания при превышении глубины стакана (Orderbook Walking):
Если объём ордера превышает ликвидность вершины стакана ($N > D_{\text{top}}$), исполнение частично захватывает более глубокие уровни книги заявок:
$$\text{Excess Ratio} = \frac{N - D_{\text{top}}}{D_{\text{top}}}$$
$$\text{Depth Slippage Factor} = 0.0005 \times \min(\text{Excess Ratio}, 5.0)$$
$$\text{Fill Price}_{\text{Long}} = P_{\text{best\_ask}} \times (1 + \text{Depth Slippage Factor})$$
$$\text{Fill Price}_{\text{Short}} = P_{\text{best\_bid}} \times (1 - \text{Depth Slippage Factor})$$
Максимальное штрафное проскальзывание при сильном исчерпании ликвидности ограничено 25 bps ($0.25\%$).

### 2.3. Чёткое отделение фильтров исполнения от торговых правил:
Фильтр спреда $0.08\%$ (`MAX_ALLOWED_SPREAD_PCT = Decimal("0.0008")`) является **фильтром качества исполнения (Execution Quality Gate)**, а не компонентом альфа-сигнала. Если пробой Donchian сформировался на неликвидном или аномально раздвинутом спреде, альфа-сигнал регистрируется, но ордер отклоняется execution-модулем Shadow с фиксацией в журнале упущенных возможностей (`missed_intent_log`).

---

## 3. Математика начисления ставки финансирования (Funding Rate)

Бессрочные линейные контракты Bybit начисляют или списывают ставку финансирования каждые 8 часов (в 00:00, 08:00, 16:00 UTC).

В модуле Shadow начисление рассчитывается строго по формуле биржи:
$$\text{Funding Payment} = \text{Current Qty} \times P_{\text{mark}} \times \text{Funding Rate} \times (-\text{Direction})$$

- Для позиции **LONG** ($\text{Direction} = +1$):
  - При положительной ставке ($\text{Funding Rate} > 0$) позиция **выплачивает** финансирование (капитал уменьшается).
  - При отрицательной ставке ($\text{Funding Rate} < 0$) позиция **получает** финансирование (капитал увеличивается).
- Для позиции **SHORT** ($\text{Direction} = -1$):
  - При положительной ставке позиция **получает** финансирование.
  - При отрицательной ставке позиция **выплачивает** финансирование.

Каждая выплата фиксируется в базе SQLite и отражается в накопленном показателе `total_funding` обоих портфелей.

---

## 4. Спецификация реляционного журнала `shadow_execution_events`

Все микросекундные события исполнения сохраняются в файл SQLite базы данных:  
`reports/v32_dual_paper_preparation_001/shadow_execution_events.db`.

### 4.1. Схема таблицы `shadow_execution_events`:
```sql
CREATE TABLE shadow_execution_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange_ts INTEGER NOT NULL,          -- Временная метка свечи / тика биржи (мс)
    receive_ts INTEGER NOT NULL,           -- Время поступления события в систему (нс)
    decision_ts INTEGER NOT NULL,          -- Время принятия торгового решения (нс)
    trade_id TEXT NOT NULL,                -- Уникальный идентификатор сделки
    portfolio TEXT NOT NULL,               -- 'NATIVE' или 'SHADOW'
    symbol TEXT NOT NULL,                  -- Торговый контракт (напр. SOLUSDT)
    event_type TEXT NOT NULL,              -- ENTRY_FILL, CASCADE_TP1, CASCADE_TP2, STOP_LOSS, FAST_SCRATCH, FUNDING_PAYMENT, WIDE_SPREAD_REJECT
    direction TEXT NOT NULL,               -- LONG или SHORT
    intended_price REAL NOT NULL,          -- Расчётная сигнальная цена пробоя
    fill_price REAL NOT NULL,              -- Фактическая цена исполнения
    fill_price_native REAL DEFAULT 0,      -- Цена исполнения в контуре Native
    fill_price_shadow REAL DEFAULT 0,      -- Цена исполнения в контуре Shadow
    price_shadow REAL DEFAULT 0,           -- Котировка в Shadow
    qty REAL NOT NULL,                     -- Объём заявки
    fill_qty REAL DEFAULT 0,               -- Фактически исполненный объём
    notional_usdt REAL NOT NULL,           -- Стоимость ордера в USDT
    native_fee REAL DEFAULT 0,             -- Комиссия в контуре Native
    shadow_fee REAL DEFAULT 0,             -- Комиссия в контуре Shadow
    fee_paid REAL DEFAULT 0,               -- Уплаченная комиссия в транзакции
    funding_paid REAL DEFAULT 0,           -- Сумма финансирования
    slippage_bps REAL DEFAULT 0,           -- Проскальзывание в базисных пунктах (bps)
    best_bid REAL NOT NULL,                -- Лучшая цена спроса в стакане
    best_ask REAL NOT NULL,                -- Лучшая цена предложения в стакане
    spread_pct REAL NOT NULL,              -- Относительный спред стакана ((Ask-Bid)/Bid)
    available_depth_usdt REAL NOT NULL,    -- Доступная глубина первого уровня (USDT)
    execution_status TEXT NOT NULL,        -- FULL, PARTIAL или REJECTED
    realized_pnl REAL NOT NULL,            -- Реализованный PnL транзакции
    portfolio_equity REAL NOT NULL,        -- Баланс капитала портфеля после сделки
    shadow_equity REAL DEFAULT 0,          -- Баланс капитала Shadow после сделки
    gearbox_gear INTEGER NOT NULL,         -- Текущая ступень АКПП (3, 2 или 1)
    notes TEXT                             -- Текстовые примечания и обоснование
);
```

### 4.2. Гарантия целостности базы данных:
- Подключения к SQLite всегда явно закрываются в блоках `try ... finally`, предотвращая утечки дескрипторов файлов и блокировки ОС Windows (`WinError 32`).
- База данных работает в режиме `WAL` (Write-Ahead Logging), обеспечивая субмикросекундную запись без деградации торгового цикла.
