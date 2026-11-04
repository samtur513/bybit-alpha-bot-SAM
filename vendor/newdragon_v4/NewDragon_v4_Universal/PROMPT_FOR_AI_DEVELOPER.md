# MASTER PROMPT: Интеграция NewDragon v4.0 Universal (Фаза 1) в Torg_Bot_3

Скопируйте и отправьте приведенный ниже блок текста AI-ассистенту / квант-разработчику в проекте **`Torg_Bot_3 -Antigraviti`**:

```text
ROLE & OBJECTIVE:
Ты — Senior Quant & Systems Developer проекта Torg_Bot_3. 
Твоя задача — изучить и внедрить проверенное математическое ядро "NewDragon v4.0 Universal (Фаза 1)" из переданного архива для радикального повышения винрейта, защиты от ликвидаций на малых депозитах ($100–$500) и сокращения комиссионных издержек Bybit V5 Linear Perpetuals.

АРХИТЕКТУРНЫЕ ИНВАРИАНТЫ:
1. Запрет Lookahead Bias: В детекторе режима (CausalRegimeDetector) используется строго каузальный Forward-шаг в логарифмическом пространстве step_causal(). Запрещено вызывать predict() или predict_proba() на полном окне истории, чтобы не заглядывать в будущее.
2. Размерность позиционирования: Все ограничители объема (True 1R, Fractional Kelly, Risk Parity, CVaR 95%) в UniversalPositionSizer рассчитываются строго в единицах базового актива (монетах) делением долларовых лимитов на entry_price перед взятием минимума.
3. Защита Micro-Account (Bybit V5 minNotional): Если рассчитанный объем q_final * entry_price < spec.min_notional (например, $100 для BTCUSDT при бюджете $100-$300), позиция ОБЯЗАНА отклоняться (возврат 0.0). Категорически запрещено завышать риск ради выполнения minNotional биржи!
4. Сохранение обратной совместимости: Существующие торговые циклы Torg_Bot_3 (Donchian Breakout v3 и Scalp OMS v3.7) не должны ломаться. Интеграция выполняется через готовые адаптеры UniversalRiskBridge и AdaptiveExitBridge.

ПОШАГОВЫЙ ПЛАН ИНТЕГРАЦИИ:
Шаг 1. Распаковка и верификация:
- Распакуй архив в корень или подпапку проекта (например, vendor/newdragon_v4/ или modules/newdragon_v4/).
- Убедись, что установлены зависимости (pip install -r requirements.txt: numpy, scipy, pandas, hmmlearn, pytest).
- Запусти тесты: pytest tests -v. Все 11 тестов должны пройти со 100% PASS (проверка Zero-Lookahead, Micro-Router, FSM).

Шаг 2. Подключение UniversalRiskBridge в расчет позиций:
- В v3/src/kelly_sizing.py или точке формирования ордера (paper_trader_v3.py / scalp_oms.py) подключи UniversalRiskBridge.
- Перед выставлением лимитного/маркет ордера вызывай size_position_for_torg_bot().
- Проверь: если на депозите $100 приходит сигнал по BTCUSDT, ордер блокируется, а по SOLUSDT / XRPUSDT / DOGEUSDT — успешно рассчитывается с учетом ступенчатого тормоза просадки (-5% -> 0.75, -10% -> 0.35, -20% -> Halt).

Шаг 3. Подключение AdaptiveExitBridge (4-Stage Exit FSM):
- При открытии сделки регистрируй позицию: exit_bridge.register_position(symbol, side, entry_price, stop_price, qty).
- На каждом баре вызывай: action = exit_bridge.on_bar_update(...).
- Обрабатывай действия:
  * FULL_CLOSE_SCRATCH: немедленный выход около нуля при затухании импульса (2 внутренних бара + встречный OFI).
  * UPDATE_STOP (Stage 1): подтяжка стопа в -0.35R при достижении +0.85R прибыли.
  * PARTIAL_CLOSE_AND_STOP (Stage 2): закрытие 60% объема лимитным ордером (по Maker-комиссии 2.0 bps) при достижении +1.40R и перенос стопа на остаток в +0.35R (гарантированный безубыточный плюс).
  * UPDATE_STOP (Stage 3): трейлинг Chandelier (2.5x ATR), динамически сжимающийся до 1.2x ATR при угрозе шока (P(shock) >= 0.25).

Шаг 4. Отчет и тестовый прогон:
- Прогони бэктест или Paper Trader симулятор на исторических данных Bybit.
- Сравни показатели чистой прибыли (Net PnL), уплаченных комиссий (Fees) и максимальной просадки (MaxDD) с базовой версией.
```
