# SHADOW_PARITY_REPORT.md
## Доказательство идентичности торговых сигналов и детерминированного воспроизведения оригинальной v3.2

**Протокол:** V32-SHADOW-INTEGRITY-002 / P1 & P2  
**Репозиторий:** `samtur513/bybit-alpha-bot-SAM`  
**Дата аудита:** 2026-11-15T00:50:00+03:00  
**Статус паритета сигналов:** PROVEN_AT_CORE_LEVEL

---

## 1. Архитектурный контекст и устранение имитаций

В предыдущей исследовательской версии Shadow модуль `CoreV3Engine` лишь инстанцировался, но фактический торговый цикл не вызывался: методы `process_orderbook_entry` и `process_orderbook_exit` принимали готовые произвольные цены и объёмы.

В настоящей реализации `v3_2/src/shadow/paper_trader_v32_shadow.py` развёрнут **полноценный торговый контур**, обеспечивающий 100% идентичность вычислений и сигналов оригинальной v3.2.

---

## 2. Точная цепочка вызовов оригинального ядра в Shadow

Вся торговая логика Shadow напрямую использует проверенные классы оригинальной архитектуры без упрощённых аппроксимаций:

```mermaid
flowchart TD
    subgraph MarketEventStream["1. Поток рыночных событий (Market Event Stream)"]
        Bar1h["Закрытый 1h бар (t)"] --> Buffer["Буфер 120 баров (Donchian)"]
        OBTop["Orderbook Top (Best Bid / Best Ask / Depth)"] --> DepthEval["Оценка глубины стакана"]
    end

    subgraph DefenseAndRegime["2. Защитный щит и классификатор режимов"]
        Buffer --> RG["News Core v1.2 (RegimeGuard)"]
        Buffer --> RC["MarketRegimeClassifier (ADX 20 / BBW 40%)"]
        RG -->|Макро-эмбарго (45 мин до / 30 мин после)| EmbargoBlock["Блокировка входов (EMBARGO_BLOCKED)"]
    end

    subgraph SignalCore["3. Вычисление сигналов и сайзинг (Signal Core)"]
        RC -->|TREND| DonchianSignal["Donchian Breakout 120 bars (Zero-Lookahead shift(1))"]
        DonchianSignal --> Filter["Triple Trend Filter: ADX >= 18.0, EMA50 > EMA200, BTC Delta"]
        Filter --> АКПП["KellyRiskManager: АКПП (G3: 100%, G2: 50%, G1: 25%)"]
        АКПП --> Sizing["Расчёт размера позиции (Half-Kelly 1.0% risk, max 4 slots)"]
    end

    subgraph TradingIntent["4. Формирование единого торгового намерения (Trading Intent)"]
        Sizing --> IntentEntry["INTENT_ENTRY (symbol, dir, intended_price, target_qty, sl, atr)"]
        Buffer --> IntentManage["INTENT_MANAGE (Cascade TP1, Cascade TP2, Fast Scratch, Runner)"]
    end

    subgraph DualExecution["5. Независимое двухконтурное исполнение (Dual Execution)"]
        IntentEntry --> NativeExec["Native Paper Execution (0.090% fee, 0.02% slip, no spread gate)"]
        IntentEntry --> ShadowExec["Realistic Shadow Execution (0.100% fee, 0.08% spread gate, depth slippage)"]
        IntentManage --> NativeManage["Native Position State"]
        IntentManage --> ShadowManage["Shadow Position State"]
    end

    subgraph LedgerAndPersistence["6. Журналирование и персистентность"]
        NativeExec --> SQLite["SQLite Event Ledger (shadow_execution_events.db)"]
        ShadowExec --> SQLite
        NativeManage --> StateJSON["Atomic State Persistence (BOT_STATE_SHADOW_PERSISTENT.json)"]
        ShadowManage --> StateJSON
    end
```

### 2.1. Оригинальные компоненты и их конфигурация:
1. **Donchian Breakout 120 Bars (1H)**:  
   - Канал рассчитывается на основе `shift(1)` по закрытым 1-часовым барам, гарантируя нулевое заглядывание вперёд (Zero-Lookahead PIT).
   - Верхний канал: `upper_channel = df['high'].shift(1).rolling(120).max()`.
   - Нижний канал: `lower_channel = df['low'].shift(1).rolling(120).min()`.
2. **Тройной фильтр тренда (Triple Trend Filter)**:  
   - `ADX >= 18.0` (проверка направленной силы движения).
   - `EMA50 > EMA200` (для LONG) и `EMA50 < EMA200` (для SHORT).
   - Фильтр корреляции с вектором движения Bitcoin (`BTC Delta Filter`).
3. **News Core v1.2 (Regime Guard)**:  
   - Полное эмбарго за 45 минут до макрособытия (CPI, FOMC, NFP) и 30 минут после него.
   - Запрет на открытие новых позиций (`block_new_entries = True`).
4. **АКПП (3-Stage Auto-Gearbox)**:  
   - `Gear 3`: Просадка $< 10\%$ $\to$ риск $1.0\%$ (Half-Kelly).
   - `Gear 2`: Просадка $\ge 10\%$ $\to$ риск $0.5\%$ (пониженная передача).
   - `Gear 1`: Просадка $\ge 16\%$ $\to$ риск $0.25\%$ (защитный тормоз).
   - Повышение передачи: только при восстановлении $\ge 50\%$ накопленной просадки.
5. **Cascade Take Profit Engine**:  
   - `TP1` ($+1.5 \times \text{ATR}$): закрытие 30% объёма позиции, перенос стопа в безубыток (`entry_price`).
   - `TP2` ($+3.0 \times \text{ATR}$): закрытие 40% объёма позиции, перенос стопа на уровень $+1.5 \times \text{ATR}$ в прибыль.
   - `Runner` (30%): удержание позиции до противоположного пробоя канала Donchian.
6. **Fast Scratch Guard**:  
   - Если позиция удерживается $\le 6$ баров и отклоняется против входа на $\ge 0.5 \times \text{ATR}$ при отсутствии восходящего импульса ($MFE < 0.5 \times \text{ATR}$), позиция немедленно закрывается с причиной `FAST_SCRATCH`.

---

## 3. Детерминированный Parity Replay: Доказательство идентичности сигналов

Для подтверждения идентичности решений оригинального ядра и Shadow был проведён детерминированный прогон на одинаковом наборе рыночных баров и стаканов.

### Результаты детерминированного теста:
1. **Тест генерации входов и фильтров**:  
   - Бары с ложным пробоем при `ADX < 18.0` или противоречии EMA отфильтрованы в 100% случаев.
   - Бары во время окна макро-эмбарго заблокированы в 100% случаев (`EMBARGO_BLOCKED`).
   - Истинные пробои генерируют идентичный сайзинг и уровень первоначального стоп-лосса:  
     $\text{Initial SL} = \text{Entry} - \text{Direction} \times 2.5 \times \text{ATR}$.
2. **Тест Cascade TP1 и Breakeven Stop**:  
   - При достижении порога $+1.5 \times \text{ATR}$ позиция закрывает ровно 30% объёма (с квантованием `ROUND_FLOOR` по шагу лота).
   - Стоп-лосс немедленно перемещается на цену входа (`entry_price`), флаг `is_breakeven_set = True`.
3. **Тест Cascade TP2 и Secured Stop**:  
   - При достижении $+3.0 \times \text{ATR}$ закрывается ещё 40% объёма.
   - Стоп-лосс фиксируется на уровне $+1.5 \times \text{ATR}$ в прибыли.
4. **Тест Fast Scratch**:  
   - При стагнации цены на барах 1..6 позиция ликвидируется без ожидания полного стоп-лосса $2.5 \times \text{ATR}$, сохраняя капитал.

Тесты зафиксированы в `tests/test_v32_shadow_integrity.py` и показывают **100% PASS**.

---

## 4. Разделение двух независимых портфелей (Native vs Shadow)

В соответствии с требованием P2, портфели **полностью изолированы**:
- Отказ в исполнении ордера в Shadow (например, по фильтру широкого спреда $>0.08\%$) **не отменяет и не искажает сделку в Native Paper**!
- В тесте `test_independent_portfolio_state_separation`:
  - На активе `WIDEUSDT` со спредом $0.1246\% > 0.08\%$ Native Paper успешно открыл позицию LONG.
  - Realistic Shadow зафиксировал отказ `WIDE_SPREAD_REJECT` и сохранил нулевую позицию.
  - На последующих барах Native Paper продолжает удерживать позицию, отслеживать TP1/TP2 и начислять PnL.
  - Realistic Shadow сохраняет свободный слот и способность войти в другой актив при появлении сигнала.
- Все расхождения позиций и эквити автоматически агрегируются и транслируются в дашборд порта 8084 через эндпоинт `/api/divergence`.
