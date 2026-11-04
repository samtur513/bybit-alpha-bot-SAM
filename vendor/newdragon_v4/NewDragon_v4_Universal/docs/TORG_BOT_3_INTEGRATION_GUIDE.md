# Руководство по интеграции Фазы 1 в Torg_Bot_3

**Модули:** `UniversalRiskBridge`, `AdaptiveExitBridge`, `CausalRegimeDetector`  
**Источник:** `c:\projects\Swod_Torg_BOT\NewDragon_v4_Universal`  
**Целевой проект:** `c:\projects\Torg_Bot_3 -Antigraviti` (`v3` Donchian Breakout и `v3_7` Scalp OMS)

---

## 1. Какую ценность Фаза 1 дает Torg_Bot_3?

1. **Защита от ликвидаций и слива на малых балансах (\$100–\$500):**
   На Bybit V5 для контрактов первого эшелона (BTCUSDT) действует ограничение `minNotional = 100 USDT`. Стандартные алгоритмы при стопе 1.5–2% пытались открывать минимальный ордер биржи, раздувая риск с положенных \$0.50–\$1.50 до \$2–\$3. Модуль `UniversalRiskBridge` жестко блокирует сделки, если расчетный объем по правилу 1R ниже биржевого минимума.
2. **Экономия до 64% комиссий при фиксации прибыли:**
   Вместо выхода по рынку (Taker 5.5 bps), `AdaptiveExitBridge` на Stage 2 (+1.4R) закрывает 60% позиции лимитным ордером по Maker-комиссии (2.0 bps).
3. **Ликвидация ложных пробоев (Fast Scratch):**
   Если импульс пробоя затухает (2 внутренних бара) и стакан разворачивается против позиции, бот выходит в околонулевой зоне, не дожидаясь срабатывания полного стоп-лосса (-1.0R).
4. **Адаптивный Chandelier Trail с защитой от шоков:**
   При достижении +2.0R включается люстра Чандлера с автоматическим сжатием шага с 2.5×ATR до 1.2×ATR при всплеске вероятности шока или оттоке открытого интереса.

---

## 2. Пошаговые примеры внедрения в код Torg_Bot_3

### Шаг 1. Расчет объема позиции перед отправкой ордера

В `v3/src/paper_trader_v3.py` или `v3_7/src/scalp_oms.py`:

```python
import sys
sys.path.insert(0, "c:/projects/Swod_Torg_BOT/NewDragon_v4_Universal")

from adapters.torg_bot_3_bridge import UniversalRiskBridge

risk_bridge = UniversalRiskBridge(
    taker_fee=0.00055,  # Bybit VIP0 Taker
    maker_fee=0.00020,  # Bybit VIP0 Maker
    max_leverage=10.0,
)

# Расчет объема перед открытием сделки
qty, rejection_reason = risk_bridge.size_position_for_torg_bot(
    symbol=symbol,
    side="Buy" if signal.side == "LONG" else "Sell",
    entry_price=entry_price,
    stop_price=stop_price,
    capital_usdt=current_equity,
    available_margin_usdt=available_margin,
    current_drawdown_pct=current_dd,
    market_regime=current_regime,  # "bull", "bear", "sideways", "shock"
    qty_step=spec.qty_step,
    min_order_qty=spec.min_order_qty,
    min_notional=spec.min_notional,
)

if qty <= 0.0:
    logger.info(f"[{symbol}] Ордер отклонен риск-фильтром: {rejection_reason}")
    return
```

---

### Шаг 2. Сопровождение открытой позиции через 4-Stage Exit FSM

В цикле обработки баров (`on_bar` в `core_v3.py` или `scalp_oms.py`):

```python
from adapters.torg_bot_3_bridge import AdaptiveExitBridge

exit_bridge = AdaptiveExitBridge()

# 1. При открытии позиции регистрируем ее в FSM
exit_bridge.register_position(
    symbol=symbol,
    side="Buy",
    entry_price=fill_price,
    stop_price=initial_stop_price,
    qty=filled_qty,
)

# 2. На каждом новом баре вызываем проверку автомата выхода
action = exit_bridge.on_bar_update(
    symbol=symbol,
    high=bar.high,
    low=bar.low,
    close=bar.close,
    prev_high=prev_bar.high,
    prev_low=prev_bar.low,
    highest_20=highest_20_bars,
    lowest_20=lowest_20_bars,
    atr_1h=current_atr,
    z_ofi=current_z_ofi,          # из модуля microstructure
    shock_prob=current_shock_prob # из CausalRegimeDetector
)

if action.action_type == "FULL_CLOSE_SCRATCH":
    logger.warning(f"[{symbol}] FAST SCRATCH: затухание импульса. Экстренный выход около нуля.")
    execute_market_close(symbol)

elif action.action_type == "UPDATE_STOP":
    logger.info(f"[{symbol}] Обновление стоп-лосса: {action.new_stop} ({action.reason})")
    update_exchange_stop_order(symbol, action.new_stop)

elif action.action_type == "PARTIAL_CLOSE_AND_STOP":
    logger.info(f"[{symbol}] STAGE 2: Частичный тейк {action.close_qty_ratio*100}% по Maker. Новый стоп: {action.new_stop}")
    execute_limit_tp(symbol, ratio=action.close_qty_ratio)
    update_exchange_stop_order(symbol, action.new_stop)
```

---

## 3. Рекомендуемый протокол апробации

1. **Режим Paper Trader (v3 / v3_7):**
   Запустить прогон на демо-счете или локальном симуляторе `Torg_Bot_3` на 7–14 дней.
2. **Метрики эффективности:**
   - **Комиссионная нагрузка:** Должна снизиться минимум на 30–45% благодаря выходам Stage 2 по Maker-ставке.
   - **Средний убыток при ложных пробоях:** Должен сократиться с $-1.0R$ до $-0.10R \dots -0.25R$ за счет Fast Scratch.
   - **Максимальная просадка (MaxDD):** Должна удержаться строго в пределах 7–10% благодаря ступенчатым тормозам $M_{DD}$.
