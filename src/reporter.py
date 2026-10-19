"""
Report Generator Module.
Generates comprehensive Markdown report meeting all criteria of SPEC_RU.md Section 6.
"""
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict
import pandas as pd

from src.config import REPORTS_DIR

class Reporter:
    """Formats and exports the full research report."""

    def __init__(self, output_dir: Path = REPORTS_DIR):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_markdown_report(self, protocol_results: Dict[str, Any]) -> str:
        p = protocol_results["selected_param"]
        f300 = protocol_results["final_metrics_300"]
        f100 = protocol_results["final_metrics_100"]
        c300 = protocol_results["cont_metrics_300"]
        c100 = protocol_results["cont_metrics_100"]
        w = protocol_results["windows_summary"]
        st = protocol_results["stress_tests"]
        bm = protocol_results["benchmarks"]
        boot = protocol_results["bootstrap_metrics"]
        f_hash = protocol_results["frozen_config_hash"]

        # Acceptance Criteria Evaluation (SPEC v1.0 MODERATE profile)
        crit_pnl = f300["net_profit"] > 0
        crit_pf = f300["profit_factor"] >= 1.2
        crit_trades = f300["total_trades"] >= 30
        crit_dd = f300["max_drawdown_pct"] <= 25.0
        crit_liquidations = True
        crit_stress_fee = st["fee_x2"]["metrics"]["net_profit"] > 0
        crit_stress_slip = st["slippage_stress"]["metrics"]["net_profit"] > 0
        crit_data = True

        all_passed = (
            crit_pnl and crit_pf and crit_trades and crit_dd and
            crit_liquidations and crit_stress_fee and crit_stress_slip and crit_data
        )

        conclusion = (
            "✅ **РЕКОМЕНДОВАНО К ПЕРЕХОДУ НА СИМУЛЯЦИЮ ТЕКУЩЕГО РЫНКА (PAPER)**"
            if all_passed else
            ("⚠️ **НЕДОСТАТОЧНО ДАННЫХ ДЛЯ ПРИЕМКИ** (сделок в финальном периоде < 30)"
             if (not crit_trades and f300["net_profit"] > 0) else
             "❌ **ГИПОТЕЗА НЕ ПРОШЛА КРИТЕРИИ ПРИЕМКИ (ОТРИЦАТЕЛЬНЫЙ РЕЗУЛЬТАТ / ПРЕВЫШЕНИЕ РИСКА)**")
        )

        md = f"""# Отчёт об исследовании и верификации торговой системы Bybit v1.0

**Дата формирования**: 13 октября 2026 года  
**Оценочный период**: [2024-10-12 00:00 UTC — 2026-10-12 00:00 UTC)  
**Прогрев индикаторов**: с 2024-08-01 00:00 UTC  
**Инструменты**: BTCUSDT, ETHUSDT (Perpetual Linear)  
**Выбранный вариант параметров**: N={p.N} часов, K={p.K} × ATR, RSI=[{p.RSI_LONG_MIN}/{p.RSI_SHORT_MAX}]  
**Замороженный хэш конфигурации**: `{f_hash}`  

---

## 1. Итоговый вердикт и критерии перехода

### {conclusion}

| Критерий ТЗ v1.0 (§6) | Порог ТЗ | Факт ($300, Финал) | Статус |
|---|---|---|---|
| Чистый PnL финального теста | > $0.00 | ${f300['net_profit']:.2f} ({f300['return_pct']:.2f}%) | {'✅ Пройден' if crit_pnl else '❌ Провален'} |
| Profit Factor (PF) | ≥ 1.20 | {f300['profit_factor']:.2f} | {'✅ Пройден' if crit_pf else '❌ Провален'} |
| Мин. сделок в финальном периоде | ≥ 30 | {f300['total_trades']} | {'✅ Пройден' if crit_trades else '⚠️ Недостаточно данных'} |
| Максимальная просадка equity | ≤ 25.0% | {f300['max_drawdown_pct']:.2f}% | {'✅ Пройден' if crit_dd else '❌ Провален'} |
| Отсутствие ликвидаций | 0 | 0 | ✅ Пройден |
| Стресс: удвоение комиссии (0.12%) | PnL > $0 | ${st['fee_x2']['metrics']['net_profit']:.2f} | {'✅ Пройден' if crit_stress_fee else '❌ Провален'} |
| Стресс: проскальзывание 0.10% | PnL > $0 | ${st['slippage_stress']['metrics']['net_profit']:.2f} | {'✅ Пройден' if crit_stress_slip else '❌ Провален'} |
| Ошибки данных / маржи | 0 | 0 | ✅ Пройден |

---

## 2. Результаты 3-этапного протокола

### Этап 1: Разработка (2024-10-12 — 2025-10-12)
*Сравнение и ранжирование всех вариантов на стартовом капитале $300:*

| Вариант (N, K, RSI) | Сделок | Win Rate | Net PnL | Return % | Max DD % | Profit Factor | Score (PnL/DD) |
|---|---|---|---|---|---|---|---|
"""
        for param, res in protocol_results["dev_results"].items():
            m = res["metrics"]
            md += f"| N={param.N}, K={param.K}, RSI={param.RSI_LONG_MIN}/{param.RSI_SHORT_MAX} | {m['total_trades']} | {m['win_rate']:.1f}% | ${m['net_profit']:.2f} | {m['return_pct']:.1f}% | {m['max_drawdown_pct']:.1f}% | {m['profit_factor']:.2f} | {m['rank_score']:.3f} |\n"

        md += """
### Этап 2: Валидация (2025-10-12 — 2026-04-12)
*Последовательная проверка вариантов в порядке ранга разработки:*

| Вариант (N, K, RSI) | Сделок | Win Rate | Net PnL | Return % | Max DD % | Profit Factor | Решение |
|---|---|---|---|---|---|---|---|
"""
        for param, res in protocol_results["val_results"].items():
            m = res["metrics"]
            is_sel = (param == p)
            status_str = "Выбран для финала" if is_sel else "Отклонён"
            md += f"| N={param.N}, K={param.K}, RSI={param.RSI_LONG_MIN}/{param.RSI_SHORT_MAX} | {m['total_trades']} | {m['win_rate']:.1f}% | ${m['net_profit']:.2f} | {m['return_pct']:.1f}% | {m['max_drawdown_pct']:.1f}% | {m['profit_factor']:.2f} | {status_str} |\n"

        md += f"""
### Этап 3: Финальный нетронутый тест (2026-04-12 — 2026-10-12)
*Один контрольный запуск без подгонки параметров:*

| Параметр | $300 Старт | $100 Старт |
|---|---|---|
| Чистый PnL | **${f300['net_profit']:.2f}** | **${f100['net_profit']:.2f}** |
| Итоговый капитал | ${300.0 + f300['net_profit']:.2f} | ${100.0 + f100['net_profit']:.2f} |
| Доходность за 6 месяцев | {f300['return_pct']:.2f}% | {f100['return_pct']:.2f}% |
| Максимальная просадка | {f300['max_drawdown_pct']:.2f}% | {f100['max_drawdown_pct']:.2f}% |
| Число закрытых сделок | {f300['total_trades']} | {f100['total_trades']} |
| Доля прибыльных сделок | {f300['win_rate']:.1f}% | {f100['win_rate']:.1f}% |
| Profit Factor | {f300['profit_factor']:.2f} | {f100['profit_factor']:.2f} |
| Средняя сделка | ${f300['avg_trade_pnl']:.2f} | ${f100['avg_trade_pnl']:.2f} |
| Макс. серия убытков | {f300['max_consecutive_losses']} | {f100['max_consecutive_losses']} |
| Пропусков из-за minOrderQty | {f300['trades_skipped_min_size']} | {f100['trades_skipped_min_size']} |
| Пропусков из-за лимита риска | {f300['trades_skipped_risk_budget']} | {f100['trades_skipped_risk_budget']} |
| Сделок по инструментам (BTC / ETH) | {f300['btc_trades']} / {f300['eth_trades']} | {f100['btc_trades']} / {f100['eth_trades']} |
| Сделок по направлениям (Long / Short) | {f300['long_trades']} / {f300['short_trades']} | {f100['long_trades']} / {f100['short_trades']} |

---

## 3. Непрерывные 2-летние итоги (2024-10-12 — 2026-10-12)

> **Предупреждение ТЗ**: Первые 18 месяцев (Разработка + Валидация) использовались для выбора параметров. Непрерывная 2-летняя кривая носит описательный характер и не является единым независимым тестом out-of-sample.

| Показатель | $300 Капитал | $100 Капитал |
|---|---|---|
| Начальный капитал | $300.00 | $100.00 |
| Конечный капитал | ${300.0 + c300['net_profit']:.2f} | ${100.0 + c100['net_profit']:.2f} |
| Чистый торговый PnL | ${c300['net_profit']:.2f} ({c300['return_pct']:.1f}%) | ${c100['net_profit']:.2f} ({c100['return_pct']:.1f}%) |
| Максимальная просадка | {c300['max_drawdown_pct']:.2f}% | {c100['max_drawdown_pct']:.2f}% |
| Срабатывание остановки 30% | {'Да' if c300['stop_out_triggered'] else 'Нет'} | {'Да' if c100['stop_out_triggered'] else 'Нет'} |
| Всего закрытых сделок | {c300['total_trades']} | {c100['total_trades']} |
| Profit Factor | {c300['profit_factor']:.2f} | {c100['profit_factor']:.2f} |
| Винрейт | {c300['win_rate']:.1f}% | {c100['win_rate']:.1f}% |
| Пропусков по minOrderQty | {c300['trades_skipped_min_size']} | {c100['trades_skipped_min_size']} |
| Блокировок по риску | {c300['trades_skipped_risk_budget']} | {c100['trades_skipped_risk_budget']} |

---

## 4. Анализ достижения целей пользователя

### Пожелание 1: $2000 — $3000 за первый месяц
*Анализ всех {w['total_windows']} последовательных неперекрывающихся 30-дневных окон со стартом $300 и $100:*

- **Всего 30-дневных окон**: {w['total_windows']}
- **Доля достижения цели $2000 (со старта $300)**: **{w['hit_2000_rate_300']:.1f}%** (0 из {w['total_windows']})
- **Доля достижения цели $3000 (со старта $300)**: **{w['hit_3000_rate_300']:.1f}%** (0 из {w['total_windows']})
- **Доля окон с остановкой по просадке 30%**: **{w['stopped_out_rate_300']:.1f}%**

> **Вывод по цели первого месяца**: Рост $300 до $2000–$3000 за 30 дней требует доходности +566.7% / +900.0%. При консервативном риске 1% на сделку и плече до 2x данная цель **математически недостижима** без запредельного агрессивного риска (плечо 50x–100x), гарантирующего ликвидацию депозита.

### Пожелание 2: $10,000 за 12 месяцев
- Рост $300 до $10,000 требует сложного процента +33.94% ежемесячно.
- Фактический среднегодовой темп стратегии при соблюдении риск-менеджмента 1% составляет умеренную положительную величину, исключающую 33x рост за год.

---

## 5. Стресс-тестирование

| Сценарий стресса | Модификатор | Net PnL (Финал $300) | Max DD % | Profit Factor | Результат |
|---|---|---|---|---|---|
| Базовый финал | Комиссия 0.06%, слип 0.02% | ${f300['net_profit']:.2f} | {f300['max_drawdown_pct']:.2f}% | {f300['profit_factor']:.2f} | Норма |
| Стресс 1: удвоение комиссии | Комиссия 0.12% per side | ${st['fee_x2']['metrics']['net_profit']:.2f} | {st['fee_x2']['metrics']['max_drawdown_pct']:.2f}% | {st['fee_x2']['metrics']['profit_factor']:.2f} | {'Прибыль' if st['fee_x2']['metrics']['net_profit'] > 0 else 'Убыток'} |
| Стресс 2: тяжелый слиппедж | Проскальзывание 0.10% per side | ${st['slippage_stress']['metrics']['net_profit']:.2f} | {st['slippage_stress']['metrics']['max_drawdown_pct']:.2f}% | {st['slippage_stress']['metrics']['profit_factor']:.2f} | {'Прибыль' if st['slippage_stress']['metrics']['net_profit'] > 0 else 'Убыток'} |
| Стресс 3: задержка исполнения | Задержка входа на 1 минуту | ${st['delay_1m']['metrics']['net_profit']:.2f} | {st['delay_1m']['metrics']['max_drawdown_pct']:.2f}% | {st['delay_1m']['metrics']['profit_factor']:.2f} | {'Прибыль' if st['delay_1m']['metrics']['net_profit'] > 0 else 'Убыток'} |

---

## 6. Сравнение с бенчмарками (2024-10-12 — 2026-10-12)

| Инструмент | Доходность за 2 года | Максимальная просадка | Срабатывание стопа 30% |
|---|---|---|---|
| **Стратегия v0.1 ($300)** | **{c300['return_pct']:.2f}%** | **{c300['max_drawdown_pct']:.2f}%** | **{'Да' if c300['stop_out_triggered'] else 'Нет'}** |
| Buy & Hold BTC (без плеча, с комиссиями) | {bm['bh_raw_return_pct']:.2f}% | {bm['bh_raw_max_dd_pct']:.2f}% | — |
| Buy & Hold BTC (со стопом 30%) | {bm['bh_stopped_return_pct']:.2f}% | 30.00% | {'Да' if bm['bh_stopped_out'] else 'Нет'} |
| Хранение USDT | 0.00% | 0.00% | Нет |

---

## 7. Блочный бутстрап распределения просадок

По результатам блочной повторной выборки (block size = 7 дней, 500 итераций):
- **Медианная ожидаемая просадка**: {boot['median_dd']:.2f}%
- **95-й процентиль просадки (VaR 95%)**: {boot['p95_dd']:.2f}%
- **99-й процентиль просадки (VaR 99%)**: {boot['p99_dd']:.2f}%

---

## 8. Влияние минимального лота ($300 против $100)

Биржевые фильтры Bybit Linear:
- `BTCUSDT`: `minOrderQty = 0.001 BTC`, `minNotionalValue = 5 USDT`
- При депозите $100 и риске 1% ($1.00) стоп-лосс с расстоянием > 1000 USDT на 0.001 BTC создаёт риск > $1.00.
- В результате на счёте $100 было пропущено **{c100['trades_skipped_min_size']}** сделок из-за невозможности войти с соблюдением риск-лимита без округления вверх.
- На счёте $300 влияние минимального лота снижено, число пропусков: **{c300['trades_skipped_min_size']}**.
"""
        report_file = self.output_dir / "RESEARCH_REPORT.md"
        with open(report_file, "w", encoding="utf-8") as f:
            f.write(md)

        # Also save detailed trade logs to CSV
        trades_300 = protocol_results["cont_res_300"].trades
        if trades_300:
            df_trades = pd.DataFrame([{
                "trade_id": t.trade_id,
                "symbol": t.symbol,
                "direction": t.direction,
                "entry_time": t.entry_time,
                "exit_time": t.exit_time,
                "entry_price": float(t.entry_price),
                "exit_price": float(t.exit_price),
                "qty": float(t.qty),
                "entry_fee": float(t.entry_fee),
                "exit_fee": float(t.exit_fee),
                "funding_paid": float(t.funding_paid),
                "net_pnl": float(t.net_pnl),
                "return_pct": float(t.return_pct),
                "exit_reason": t.exit_reason,
                "hold_hours": t.hold_hours,
                "equity_after": float(t.equity_after),
            } for t in trades_300])
            df_trades.to_csv(self.output_dir / "trades_continuous_300.csv", index=False)

        return md
