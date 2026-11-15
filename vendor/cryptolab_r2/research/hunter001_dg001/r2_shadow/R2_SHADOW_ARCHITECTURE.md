# R2-SHADOW Integration Preparation — архитектура

Статус: **OFFLINE_PREPARED / VPS_PENDING** после успешного `verify.py`. Это подготовленный офлайн-пакет, а не развёртывание. Источник — приватный CryptoLab, commit `815d6884980791309cb37770ac96a9a93a2c48c0`. Экономический статус R2: **INCONCLUSIVE / REAL CAPITAL NO-GO**.

## 1. Неизменяемая исследовательская часть

Исходные `r2_validation/core.py`, `execution.py`, `observer.py`, `public_data.py`, `PREREGISTRATION.json`, `DEPLOYMENT_STATUS.json`, `runtime_requirements.txt` и родительские `t01_profit_rank/profit_rank.py`, `PREREGISTRATION.json` используются побайтово без изменений. `FROZEN_MANIFEST.json` связывает каждый файл с исходным Git blob SHA и SHA256; запуск проверяет оба хеша. Python 3.12, NumPy 2.5.4; оптимизированный Python с отключёнными assert запрещён.

R2 остаётся Bybit Spot BTC/ETH/SOL, LONG/Cash без плеча. Не меняются TSMOM28, Momentum/Volatility Score, тай-брейки, веса, комиссии, границы закрытого OOS и статистические критерии. Решение после 00:00:05 UTC; исполнение-оценка в 01:00 UTC; окна 5 минут/60 секунд, свежесть/межсимвольный перекос стаканов 5 секунд сохраняются. Никакого backfill.

Сохраняются все 24 независимых виртуальных профиля: BASELINE/R2 × 300/1000/10000 USDT × BASE/STRESS × MODEL_C5/OBSERVED_BOOK. Машиночитаемый перечень — `R2_24_ACCOUNTS_MAPPING.csv`. BASELINE — собственный T01-контроль, не четвёртая конкурирующая система. Главный профиль панели: `R2:300:BASE:OBSERVED_BOOK`; рядом STRESS и `BASELINE:300:BASE:OBSERVED_BOOK`. Деньги и состояния профилей не объединяются.

## 2. Новая обвязка

| Компонент | Ответственность |
|---|---|
| runtime.py | Проверка источников, отдельный data-dir, уникальный журнал/lock, восстановление, будущий T0, закрытые launch gates |
| raw_store.py | Потоковое сохранение полных HTTP bodies, gzip внутри дневных SQLite-сегментов, SHA256, recovery, quotas |
| telemetry.py | Только чтение согласованного SQLite snapshot и атомарная запись allowlist JSON |
| verify.py / test_shadow.py | Офлайн-проверки, фикстуры, ресурсное измерение, воспроизводимая упаковка |
| AUTHORIZATION.json | Все разрешения false; изменение файла само по себе не заменяет согласие пользователя |
| COHORT_PLAN.json | Опубликованный кандидат T0; не активирует расписание |

Runtime получает класс Observer из исходного модуля. Его signal/decide/execute/tick/trade_account и SQLite Store не переписаны. Подменён только транспорт/архив raw; SQLite дополнительно использует synchronous=FULL. Обвязка не импортирует Demo-контур и TORG_BOT.

Новый data-dir создаётся только при его отсутствии. Повторный prepare завершается ошибкой; функции сброса капитала нет. В каталоге находятся независимые `observer.sqlite`, WAL, lock, COHORT, IDENTITY, START_RECEIPT, heartbeat, исходные CSV-экспорты, raw-сегменты и telemetry.json. Работа с существующей v3.2/8083 не требуется.

## 3. Журнал и recovery

Исходный Observer фиксирует решение/снимки/ордера-оценки/счета/маркировку и deterministic event IDs одной SQLite-транзакцией. EXECUTION id фиксируется вместе со всеми 24 счетами. Сбой до COMMIT откатывает шаг; сбой после COMMIT не повторяет его. Уже пропущенные окна фиксируются как MISSED; цены задним числом не подставляются.

Перед возобновлением обвязка проверяет SQLite integrity, точную матрицу профилей, независимую цепочку cash/qty по журналу ORDER, соответствие текущему checkpoint, связь decision/event и наличие SHA256 исходных снимков в raw. Повреждение блокирует возобновление; восстановление из догадок не выполняется. Raw может быть зафиксирован раньше торговой транзакции: лишняя подтверждённая raw-запись допустима, обратная потеря raw обнаруживается.

Один процесс имеет исходный OS-lock. После штатной или аварийной остановки состояния, риск-флаги, peak и drawdown не сбрасываются. Тест hard exit проверяет SQLite recovery незавершённой транзакции. Это не испытание физической потери питания или повреждения накопителя.

## 4. Компактное хранение без удаления первичных данных

HTTP body читается блоками 64 KiB в fsync-спул gzip. Затем compressed bytes потоково записываются через SQLite blob; receipt и body атомарно коммитятся вместе. В receipt остаются исходные URL/параметры, HTTP status/headers, request started, фактическое завершение получения/ingestion, monotonic RTT, длина и SHA256 всех исходных байтов. Это observed HTTP availability, не историческое время публикации.

Один `raw-YYYY-MM-DD.sqlite` хранит все receipts за день, порядок rowid и полные gzip bodies. Для каждого тела проверяется SHA256 исходного содержимого и compressed representation. Снимки Observer ссылаются на исходный SHA256. После проверки COMMIT временные файлы удаляются; постоянные первичные данные не удаляются.

Ошибки HTTP, API, JSON и ответы свыше исходного semantic cap 1 MB сохраняются полностью до отказа в использовании. При сетевом обрыве или исчерпании quota частичные bytes остаются с явным incomplete-состоянием: они не принимаются за полный ответ и не используются для торговли. Recovery отличает такой случай от сбоя после успешного COMMIT и идемпотентно завершает упаковку.

Завершённый прошлый день можно явно seal: SHA256 всего сегмента сохраняется рядом, дальнейшая запись запрещена. Активный день не seal-ится. Планового удаления и потери checksum нет. При переносе нужны сегменты, seal-файлы, вся база журнала и все metadata/receipts, а не только CSV. Backup живой SQLite делается SQLite backup API в будущем разрешённом эксплуатационном этапе; копирование открытого .sqlite без WAL не является backup.

## 5. Ресурсы

Предварительные лимиты: raw 8 GiB, дневной сегмент 256 MiB, весь runtime 10 GiB, минимум 512 MiB свободного SSD; raw не более 2000 файлов, runtime не более 3000. Gate останавливает сбор/шаг до дальнейшего расхода; он не удаляет данные для освобождения места. Потребуется место для временного полного ответа и транзакционного журнала; checks намеренно консервативны.

За 184 дня при обычном одном сегменте/день — 184 сегмента + до 184 seal-файлов, transient spools и SQLite journals. Это порядок сотен постоянных файлов вместо порядка 800 тысяч отдельных gzip. SQLite содержит полные данные; экономия inode не означает гарантированную экономию SSD при любых payload.

`evidence/RESOURCE_PROFILE.json` содержит измерение 4410 синтетических ответов, размер и число файлов. Экстраполяция там справедлива только для того же payload. Python allocation peak не равен RSS; live bandwidth, CPU, RSS, рост CSV/DB и влияние на другие процессы не измерялись. Исходные оценки RAM 64–128 MB/лимит192 MB и запросов около4410/сутки остаются ориентирами, не VPS-гарантиями. Raw recovery проверяет все bodies и может занять заметное время после шести месяцев.

## 6. Read-only граница с TORG_BOT

Найденный источник требований: `samtur513/bybit-alpha-bot-SAM`, commit `23d2c40f57379cb4f30c3ca776d49eae9307ab62`, `reports/v32_dual_paper_preparation_001/09_DASHBOARD_SPECIFICATION.md`: JSON endpoints status/trades/events и отдельный meta dashboard. Этот исходный документ не задаёт точную схему R2.

Подготовленная минимальная спецификация — `TELEMETRY_CONTRACT.md`, `telemetry.schema.json`, synthetic/null example. В TORG_BOT разрешено публиковать только эти три файла, без исходников/конфигураций/данных CryptoLab и без приватных source SHA. Получатель позже читает только атомарный `telemetry.json` через read-only mount/отдельного пользователя; доступа к базе и raw ему не требуется. R2 не слушает HTTP-порт, не меняет 8083 и не предоставляет control endpoint. Конкретное подключение к общей панели остаётся приёмочным Gate, а не выполненным VPS-действием.

Экспорт содержит только три агрегированных профиля, Equity/PnL/MDD/cash/экспозицию, costs/turnover/rejects, последние публичные signal score/weights, ошибки/пропуски и R2−T01 в USDT/п.п. Profit Factor null, пока нет корректно определённой лотовой реализации. Устаревшая/отсутствующая оценка не подменяется последней удобной ценой. Межсистемные капиталы и PnL не суммируются.

## QA FIX-001 — актуализация оценок и admission evidence

Этот раздел уточняет реализацию после принятого коммита a2f1820d9b6da02c3f24cc8a5c1680016ea2b54e.

### Отдельная текущая оценка

marks.py хранит только последний полный набор уже полученных и проверенных Observer котировок BTC/ETH/SOL. runtime.py оборачивает вызов markets(), сохраняя его исходный результат без изменений. Нового poll, HTTP-запроса, сделки, ребаланса, записи Equity или изменения risk/MDD/24 счетов эта обвязка не делает. Ошибка дополнительного cache не изменяет исходное исполнение.

Производственный export получает этот cache и один read-only SQLite snapshot текущих cash/qty. Equity = cash + сумма qty × mid. BASE/STRESS/T01 оцениваются по одному набору цен; их разница согласована в рамках того же снимка остатков. Часовая Equity в исходном журнале остаётся историей и не определяет свежесть текущего экрана.

valuation_at — самое раннее реальное book event time из этого набора, не время экспорта. Время получения не может предшествовать source event; будущее, неполный набор, перекос/ошибка цены исключаются. Максимальный возраст каждого source quote остаётся120 секунд. Экспорты не продлевают его. После истечения или при пустом cache текущая оценка/null не подменяется старой часовой Equity; после рестарта без новой котировки также null. История не пересчитывается задним числом.

По BBO не заявляется текущая ликвидируемость всего портфеля: liquidation/dust в этом read-only текущем представлении null. Исторические frozen-оценки и их source snapshots сохранены в журнале. MDD/risk остаются именно состоянием исходного Observer.

Wire-format r2-shadow-telemetry/1, существующие поля, schema и контракт не изменены. В TORG_BOT в QA FIX-001 изменений нет.

### Проверяемые результаты до будущего запуска

readiness.py требует evidence packet в независимом data-dir/readiness до lock, DB-init, создания start receipt и HTTP. Все разрешающие boolean-флаги сами по себе недостаточны. Collector collect_readiness.py подготовлен только для будущего отдельно разрешённого этапа: в этой работе он с разрешающей policy не выполнялся.

Обязательные файлы: manifest.json, linux.json, linux_trace.json, functional.json, resources.json и полные raw-ответы9 public запросов. SHA256 manifest должен быть явно закреплён в policy. Manifest связывает результаты с fingerprint хоста/boot/kernel, точным runtime path, cohort/T0, frozen manifest и текущим набором production source hashes. Возраст не более1 часа; будущие timestamps запрещены.

Linux result связан с хешем известной probe: Python3.12/NumPy2.5.4, реальная OS-lock exclusivity, WAL/SQLite integrity и hard-exit rollback. Проверяются structured result, returncode и хешированный stdout/stderr trace; системная среда должна соответствовать текущему Linux-хосту.

Functional result заново воспроизводится из всех9 archived bodies/receipts:3 kline +3 instruments +3 books. Проверяются HTTPS api.bybit.com/Spot/symbol/window, HTTP/API status, SHA256, завершённые29 дневных свечей, свежесть/перекос книг на момент проверки. Пересчитываются weights, trade_account/valuation и все24 результата. Нельзя заменить их одной отметкой PASS или количеством профилей. Это CONTROL_ONLY, без prospective account mutations.

Resource report содержит измеренные bytes RAM/disk/inodes, fd limit, load/CPU и расход runtime. Проверяются опубликованный результат и текущие измерения. Для admission: свободная RAM>=512MiB, inode>=4096, fd>=1024, load/CPU<=0.8, достаточно SSD под оставшийся10GiB budget плюс512MiB. При последующей работе текущие resource floors проверяются перед каждым tick. Это conservative gates, не гарантия непрерывности всех соседних систем.

Фактическое разрешение пользователя остаётся отдельным условием: deployment_authorized плюс user_authorization с scope R2_SHADOW_START, reference и точным T0. Ни collector, ни результаты тестов не создают это разрешение. Default policy содержит false/null; фактические VPS/Linux/functional/resource результаты сейчас отсутствуют. Статус VPS_PENDING сохраняется.
