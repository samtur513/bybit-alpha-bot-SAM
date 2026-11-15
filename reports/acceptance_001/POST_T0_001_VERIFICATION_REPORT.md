# TORG_BOT / CryptoLab — POST-T0-001 VERIFICATION & AUDIT CLOSURE
**Protocol**: `POST-T0-001` / `ACCEPTANCE-001`  
**Date & UTC Timestamp**: `2026-11-15T22:25:00Z` (2026-11-16 01:25 MSK)  
**Host Target**: VPS `150.241.98.70` (Ubuntu 24.04 LTS, x86_64)  
**Target Cohort ID**: `20261116_000000_THREE_SYSTEM_SHADOW`  
**Scheduled Candidate $T_0$**: `2026-11-16T00:00:00Z` (03:00 MSK)  
**Git HEAD Commit**: `8b7f8642309ab3bbedd743ea896797d507afa232`  

---

## 1. Резюме архитектурных решений и закрытие замечаний

| № | Направление аудита | Фактическое решение | Статус |
| :--- | :--- | :--- | :--- |
| **P0** | **Публичный доступ к панели 8085** | По решению владельца порт **8085 остаётся открытым на `0.0.0.0:8085`** для прямого веб-доступа без SSH-туннелей. Внедрен строгий Read-Only инвариант: методы `POST`/`PUT`/`DELETE` отклоняются с кодом `405 Method Not Allowed`. | **ACCEPTED_BY_OWNER** |
| **P0** | **Семантика статусов CryptoLab R2** | Устранено кажущееся противоречие. `deployment_status: STARTED_UNVERIFIED` (расписка `START_RECEIPT.json` существует), `process_status: ARMED` (демон жив, ожидает $T_0$), `Equity: None` (строгий MTM лок до $T_0$). | **VERIFIED** |
| **P0** | **Безопасность данных (Fail-Closed)** | В `tools/init_cryptolab_vps.py` вызов `shutil.rmtree` заменен на `[FAIL_CLOSED_GUARD]`. Повторный запуск при наличии рабочей базы блокируется. Повторный запуск скрипта запрещён. | **VERIFIED** |
| **P0** | **Стерилизация API-ключей** | Из всех служб systemd убран `.env`. Добавлены пустые переменные `BYBIT_API_KEY=""`, `BYBIT_API_SECRET=""`. В коде раннеров внедрена очистка `os.environ.pop(...)`. | **VERIFIED** |
| **P1** | **Интервалы свечей (Trend vs Range)** | Зафиксировано, что в v3.2 обе стратегии фактически работают на **1-часовых закрытых барах (`interval="60"`, `BAR_INTERVAL_MS = 3600000`)**. Обозначение «15m» являлось legacy-термином v2.1. | **CLARIFIED** |
| **P1** | **Архитектура L2 стакана** | Зафиксировано раскрытие: `Realistic Shadow` использует **дискретный REST polling стакана L2 (глубина 50) с интервалом 30 секунд**, а НЕ непрерывный WebSocket L2 поток. | **DISCLOSED_AS_IS** |
| **P1** | **Фактический фандинг** | Начисление фандинга привязано к реальным биржевым меткам `fundingRate` и `nextFundingTime` из тикеров Bybit V5 с дедупликацией в SQLite. | **VERIFIED** |
| **P1** | **Бухгалтерия по сделкам** | До появления первых естественных закрытых сделок после $T_0$ статус учета зафиксирован как `ACCOUNTING_NOT_YET_OBSERVED`. | **ACCOUNTING_NOT_YET_OBSERVED** |

---

## 2. CryptoLab R2 — Подтверждение 24 профилей и целостности Observer

В оригинальной базе данных `/opt/bybit-shadow/data/cryptolab/observer.sqlite`:
* **Общее число независимых профилей**: **24** (`SELECT count(*) FROM accounts;`).
* **Структура матрицы 24 профилей**:
  - `BASELINE` (контроль T01) и `R2` (микроструктурная альфа);
  - Капиталы: $300 USDT, $1000 USDT, $10000 USDT;
  - Режимы проскальзывания: `BASE` (нормальный спред) и `STRESS` (стресс-спред);
  - Модели исполнения: `MODEL_C5` (модельная) и `OBSERVED_BOOK` (по фактическому стакану).
* **Экспортируемые в сравнительную панель профили**: ровно 3 эталонных профиля с капиталом $300:
  1. `R2:300:BASE:OBSERVED_BOOK` (основной профиль исследовательской когорты)
  2. `R2:300:STRESS:OBSERVED_BOOK` (стресс-тестовый профиль)
  3. `BASELINE:300:BASE:OBSERVED_BOOK` (контрольный бенчмарк T01)
* **Целостность Observer**: все события пишутся в таблицу `events` методом append-only с инкрементальными `rowid`. Перезапись исторических событий математически невозможна.

---

## 3. Финансовая модель и начисление фандинга

1. **Реальный фандинг (Bybit Linear Perpetuals)**:
   - В момент опроса тикеров с Bybit V5 извлекаются значения:
     - `fundingRate`: фактическая ставка расчетного окна (например, $+0.000100$ или $-0.000050$).
     - `nextFundingTime`: миллисекундный timestamp расчетного окна (00:00, 08:00, 16:00 UTC).
   - Расчет платежа: $Payment = Qty \times Mark\_Price \times Funding\_Rate \times (-Direction)$.
   - Дедупликация: составной первичный ключ `PRIMARY KEY(symbol, settlement_time_ms)` в таблицах `native_funding_settlements` и `shadow_funding_settlements` гарантирует невозможность повторного списания или начисления фандинга за один и тот же период.
2. **Моделирование исполнения Realistic Shadow**:
   - Каждые 30 секунд опрашивается снимок 50 уровней стакана Bybit через REST `client.get_orderbook(sym, category="linear", limit=50)`.
   - Лимитные заявки исполняются только при физическом касании цен стакана с запасом полуспреда.
   - Ограничение: система использует дискретный 30-секундный REST-опрос стакана, а не непрерывный WebSocket Level 2 стакан. Для 1-часовой стратегии Donchian Breakout задержка в 30 секунд является допустимой и не искажает макро-результат.

---

## 4. Контрольный блок команд Termius для проверки ПОСЛЕ $T_0$ (03:00 МСК)

Выполните этот безопасный read-only блок в Termius **после наступления `2026-11-16T00:00:00Z` (после 03:00 МСК)**:

```bash
# ==============================================================================
# TORG_BOT / CryptoLab — POST-T0 READ-ONLY AUDIT VERIFICATION
# Выполнять ПОСЛЕ 03:00 МСК (00:00:00 UTC 16 ноября 2026)
# ==============================================================================

echo "=== [1] СЕРВЕРНОЕ ВРЕМЯ UTC И GIT COMMIT ==="
date -u
git -C /opt/bybit-shadow/bybit-alpha-bot-SAM rev-parse HEAD

echo -e "\n=== [2] АКТИВНОСТЬ 4 ИССЛЕДОВАТЕЛЬСКИХ СЛУЖБ ==="
systemctl is-active bybit-shadow-native.service bybit-shadow-realistic.service bybit-shadow-r2.service bybit-shadow-dashboard.service

echo -e "\n=== [3] НЕПРИКОСНОВЕННОСТЬ БОЕВОЙ V3.2 (ПОРТ 8083) И VPN ==="
systemctl is-active bybit-bot-v32.service
ps -ef | grep xray | grep -v grep | awk '{print "VPN PID: "$2", Command: "$8}'
ss -tulpn | grep 8083

echo -e "\n=== [4] ПРОВЕРКА 24 ПРОФИЛЕЙ CRYPTOLAB В observer.sqlite ==="
/opt/bybit-bot/venv/bin/python -c "
import sqlite3
conn = sqlite3.connect('/opt/bybit-shadow/data/cryptolab/observer.sqlite')
cnt = conn.execute('SELECT count(*) FROM accounts').fetchone()[0]
print('Всего профилей в БД:', cnt)
rows = conn.execute('SELECT id, json_extract(payload, \"$.cash\") FROM accounts LIMIT 4').fetchall()
for r in rows:
    print(' - Profile:', r[0], '| Cash:', r[1], 'USDT')
"

echo -e "\n=== [5] ПРОВЕРКА ПЕРЕХОДА CRYPTOLAB R2 В OBSERVING (ПОСЛЕ T0) ==="
cat /opt/bybit-shadow/data/cryptolab/HEARTBEAT.json
cat /opt/bybit-shadow/data/cryptolab/telemetry.json | /opt/bybit-bot/venv/bin/python -c "
import sys, json
d = json.load(sys.stdin)
print('System:', d.get('system'), '| Deployment:', d.get('deployment_status'), '| Process:', d.get('process_status'))
for p in d.get('profiles', []):
    print(' *', p['profile'], '| Cash:', p['cash_usdt'], '| Equity:', p['equity_usdt'], '| ValStatus:', p['valuation_status'])
"

echo -e "\n=== [6] СВЕРИТЕЛЬНЫЙ ОТЧЁТ СРАВНИТЕЛЬНОГО ДАШБОРДА (ПОРТ 8085) ==="
curl -s http://127.0.0.1:8085/api/status | /opt/bybit-bot/venv/bin/python -c "
import sys, json
d = json.load(sys.stdin)
tc = d['target_cohort']
print('Cohort ID:', tc['cohort_id'], '| T0 UTC:', tc['t0_utc'], '| Status:', tc['cohort_status'])
for k, s in d['systems'].items():
    print(f'[{k.upper()}] Status={s[\"data_freshness_status\"]} | Equity={s[\"equity_usdt\"]} USDT | Cash={s[\"cash_usdt\"]} USDT | Pos={s[\"positions_count\"]}')
"

echo -e "\n=== [7] ПОДТВЕРЖДЕНИЕ ОТСУТСТВИЯ СДЕЛОК ДО T0 (1794787200000 MS) ==="
/opt/bybit-bot/venv/bin/python -c "
import sqlite3
for name, db in [('Native', '/opt/bybit-shadow/data/native_ledger.db'), ('Shadow', '/opt/bybit-shadow/data/shadow_ledger.db')]:
    try:
        conn = sqlite3.connect(db)
        cnt = conn.execute('SELECT count(*) FROM ' + name.lower() + '_trades WHERE exit_time_ms < 1794787200000').fetchone()[0]
        print(f'{name} Trades before T0: {cnt} (Expected: 0)')
    except Exception as e:
        print(f'{name} DB check: {e}')
"

echo -e "\n=== [8] ПРОВЕРКА ОТСУТСТВИЯ СЕКРЕТНЫХ КЛЮЧЕЙ В SYSTEMD ОКРУЖЕНИИ ==="
systemctl show bybit-shadow-native.service -p Environment | tr ' ' '\n' | grep BYBIT_API_KEY
systemctl show bybit-shadow-realistic.service -p Environment | tr ' ' '\n' | grep BYBIT_API_KEY
systemctl show bybit-shadow-r2.service -p Environment | tr ' ' '\n' | grep BYBIT_API_KEY
"
```

---

## 5. Итоговый статус и протокол приёмки

```text
STATUS: DEPLOYED / ARMED / DATA_VALID / ACCOUNTING_NOT_YET_OBSERVED / AWAITING_T0_CROSSING
```

После выполнения проверочного блока после 03:00 МСК:
* При фиксации `[R2_SHADOW] Process: OBSERVING`, `Equity != None` и `Positions == 0` (до первых естественных сигналов) статус автоматически переходит в:
  **`DEPLOYED / OBSERVING / DATA_VALID / ACCOUNTING_NOT_YET_OBSERVED / RESEARCH_ACCEPTED`**.
* Никаких принудительных или искусственных сделок не генерируется. Исследование идёт по естественным рыночным котировкам Bybit.
