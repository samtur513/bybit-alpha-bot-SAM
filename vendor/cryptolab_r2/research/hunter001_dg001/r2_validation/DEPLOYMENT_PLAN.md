# План развёртывания R2 Paper Observer

**Статус: NOT DEPLOYED. Документ содержит будущие действия; команды на VPS не выполнялись. До отдельного разрешения пользователя установка, SSH-подключения и запуск запрещены.** Локальный проверочный процесс остановлен до планировавшегося старта. Публичные контрольные снимки не являются перспективным исследованием и не подтверждают круглосуточную работу.

## Готовность локального этапа

История 2022–2023, исходные данные, независимый денежный аудит и замороженные экономические результаты опубликованы. Исходный OOS января–сентября 2026 закрыт. Стратегия и прежние исследования не изменены. По экономике R2 остаётся INCONCLUSIVE, реальный капитал NO-GO. 135 тестов включают отказ daemon до отдельного разрешения без создания БД или сетевых запросов.

DEPLOYMENT_STATUS.json — актуальный запрет запуска; старые FORWARD_START_RECEIPT и HEARTBEAT сохраняются как датированные свидетельства локальной проверки, а не текущий статус. Для запуска требуется новый опубликованный операционный addendum с разрешением и будущей датой, совпадающей с authorized_start_utc. Стратегические параметры PREREGISTRATION.json не меняются. Если прежняя дата прошла, её нельзя использовать как начало нового подтверждающего наблюдения. Шесть месяцев отсчитываются от нового фактического, заранее опубликованного старта; backfill запрещён.

## Минимальные файлы

Детерминированный пакет deployment/R2_OBSERVER_RUNTIME.zip создаётся build_deployment_bundle.py. Его manifest.json перечисляет SHA256 каждого файла; хеш архива находится в DEPLOYMENT_BUNDLE_MANIFEST.json и SHA256SUMS.txt.

| Путь в пакете | Назначение |
|---|---|
| r2_validation/observer.py | Только публичный observer, SQLite, виртуальные счета |
| r2_validation/public_data.py | Ограниченный клиент публичных GET и сохранение происхождения |
| r2_validation/core.py | Неизменный сигнал и импорт исследовательской математики |
| r2_validation/execution.py | Общая модель расходов/ограничений BASELINE и R2 |
| r2_validation/PREREGISTRATION.json | Замороженные параметры исследования |
| r2_validation/DEPLOYMENT_STATUS.json | NOT DEPLOYED, разрешение отсутствует |
| r2_validation/runtime_requirements.txt | Только numpy==2.5.4 |
| t01_profit_rank/profit_rank.py | Чистая исследовательская математика, без запуска main/load |
| t01_profit_rank/PREREGISTRATION.json | Её неизменные константы |
| manifest.json | Контрольные суммы и состав |

Торговые ядра, ключи, учётные записи, .env, SSH/VPN-конфигурации, другие сервисы, старые рыночные датасеты и БД локальных controls в пакет не входят. Это не перенос всего CryptoLab. Исторические и статистические тяжёлые расчёты остаются локальными. Отчёты по будущей БД строятся локально через forward_report.py после разрешённого получения исследовательских данных.

Зависимости: проверен Windows Python 3.12.10 + NumPy 2.5.4. На Linux совместимость Python 3.12 и готового NumPy wheel ещё НЕ проверена. Нужны существующие python3, venv/ensurepip, CA certificates, SQLite в stdlib. Runtime не требует pytest, pandas, Docker, Redis, веб-сервера, биржевого SDK или GPU. Если подходящего Python/wheel нет, остановить установку и оценить отдельно; не обновлять системный Python и не компилировать NumPy на занятой VPS.

## Измерения и бюджет

LOCAL_RESOURCE_PROFILE.json воспроизводится командой:

~~~powershell
$env:OPENBLAS_NUM_THREADS='1'
$env:OMP_NUM_THREADS='1'
python r2_validation/resource_probe.py
~~~

Измерено локально на реальных сохранённых controls: 6000 проверок виртуальных счетов, 1.875 CPU-секунды, 1.926 секунды wall time, peak RSS 36,139,008 байт (~34.5 MiB); ~0.0075 CPU-секунды на пакет 24 счетов. Ранее простаивавший до старта daemon занимал ~35.7 MiB. Это не Linux-бенчмарк, не длительный тест и не измерение сети/TLS. Контрольные сигналы были flat; активные покупки/продажи проверены unit tests, их длительная нагрузка не измерена.

| Ресурс | Предварительный бюджет / ограничение |
|---|---|
| CPU | Один процесс, BLAS/OMP по 1 потоку, до 3 сетевых worker; systemd CPUQuota=5%, Nice=19 |
| RAM | План 64–128 MiB; MemoryHigh=128M, MemoryMax=192M; проверить на целевой VPS |
| Код и окружение | Пакет <100 KiB; резерв 512 MiB на отдельный venv, wheel и временные файлы, реальный размер проверить |
| Публичные запросы | Около 4410/сутки: 4320 BBO +72 metadata +18 decision/execution; в среднем ~0.051/s, всплеск до 3 параллельно |
| Сырые файлы | Около 807030 gzip-файлов за консервативные 183 дня; отдельный inode/файл |
| Raw + receipts | Оценка ~19.9 MiB/сутки, ~3.56 GiB за183 дня при блоках4KiB и наблюдённом максимальном control gzip1222 байта |
| Общий диск | Предварительно выделить 8 GiB на состояние/архив/SQLite/WAL/CSV/логи и >1 млн свободных inode; плюс 512 MiB окружение |
| Сеть | Плановый резерв <=25 MiB/сутки полезных ответов; TLS/DNS поверх этого. Это бюджет, не лимит текущего клиента |

Дисковая оценка использует сохранённые 50-level controls как приближение для более частого level1 и не является верхней границей. Сам клиент допускает до1MB на ответ: аварийная теоретическая нагрузка намного выше. Ошибки/логи и длительное хранение не ограничены встроенной квотой. Размер, inode, latency и ошибки необходимо проверять; при выходе за бюджет остановить только observer, сохранить все данные и зафиксировать пропуск. Автоудаления архивов нет. Никакого обещания бесконечно малого диска или доказанной непрерывности.

## Условия допуска существующей VPS

Текущие CPU/RAM/диск/сеть VPS неизвестны. До отдельного разрешения не проверять их. После разрешения сначала только read-only осмотр: ОС, Python, доступная RAM, свободные bytes/inode, синхронизация UTC, load/vmstat и агрегированные метрики действующих сервисов без их приватных параметров. Нельзя останавливать, обновлять или перезапускать чужие сервисы, менять VPN/firewall/маршруты, Docker или системный Python.

Предварительный входной бюджет: >=512MiB MemAvailable, >=8.5GiB свободного места сверх уже нужного действующим сервисам, >1млн inode, здоровые clock/NTP, отсутствие swap pressure и >=20% CPU idle на 15-минутном baseline. Это операционные бюджеты нового наблюдателя, не подбор стратегии. При несоответствии — BLOCKED VPS и оставить NOT DEPLOYED. Не освобождать место удалением чужих файлов.

После разрешения — короткий canary в controls-only режиме на той же машине; сравнить 15 минут до/после. Если действующий сервис ухудшился, растёт swap, load/latency или наблюдатель не укладывается в бюджет — остановить только его. До такого измерения нельзя обещать отсутствие влияния. CPU/RAM caps и низкий приоритет уменьшают риск, но не доказывают нулевое влияние.

## Сеть и права

Рабочие соединения: исходящий HTTPS TCP443 только к api.bybit.com, DNS через существующий resolver. Только GET /v5/market/kline, /v5/market/instruments-info, /v5/market/orderbook, /v5/market/time (последний разрешён клиентом, не постоянный poll). Только category=spot и BTCUSDT/ETHUSDT/SOLUSDT, без подписи/ключа. Нет входящих портов, WebSocket, webhook, почты или внешних уведомлений.

Установка venv при разрешении потребует HTTPS к pypi.org/files.pythonhosted.org, либо заранее подготовленных проверенных wheel через уже разрешённый канал. Никаких GitHub-токенов, паролей или приватных ключей в пакете/командах/логах. Этот этап GitHub-публикации не является серверной установкой.

Непривилегированный отдельный пользователь; чтение только нового каталога app/venv, запись только state и собственного журнала. Root не нужен для самого observer. Создание отдельного пользователя, установка недостающего Python/venv, включение linger и настройка cgroup требуют отдельного админ-действия после разрешения. Не использовать root или пользователя торгового ядра для runtime. Наличие CPU/memory controller и прав user-systemd проверить; при невозможности изоляции — BLOCKED, не запускать без ограничений.

## Команды — только после отдельного разрешения

Ни одна из следующих команд на сервере не выполнялась. Имена каталога/пользователя и RELEASE_COMMIT_SHA согласуются в разрешении. Сначала опубликовать будущий start и операционное разрешение, заново собрать пакет и передать только этот пакет существующим согласованным каналом. Поставляемый сейчас пакет сохраняет NOT DEPLOYED и daemon откажет.

Read-only preflight:

~~~sh
date -u
uname -sm
python3 --version
python3 -c 'import sqlite3,ssl,venv; print(sqlite3.sqlite_version); print(ssl.OPENSSL_VERSION)'
timedatectl show -p NTPSynchronized --value
free -m
df -h .
df -i .
uptime
vmstat 1 60
systemctl --user is-system-running
~~~

Подготовка нового выделенного каталога, без системных установок и pip --user:

~~~sh
r2_root="$HOME/cryptolab-r2"
test ! -e "$r2_root/app"
mkdir -p "$r2_root/app" "$r2_root/state"
chmod 700 "$r2_root" "$r2_root/state"
sha256sum R2_OBSERVER_RUNTIME.zip
# Compare exactly with the hash in the approved GitHub commit before extraction.
python3 -m zipfile -e R2_OBSERVER_RUNTIME.zip "$r2_root/app"
python3 -m venv "$r2_root/venv"
"$r2_root/venv/bin/python" -m pip install --only-binary=:all: -r "$r2_root/app/r2_validation/runtime_requirements.txt"
cd "$r2_root/app"
"$r2_root/venv/bin/python" -c 'import json,hashlib,pathlib; p=pathlib.Path("."); m=json.loads((p/"manifest.json").read_text()); assert all(hashlib.sha256((p/n).read_bytes()).hexdigest()==h for n,h in m["files"].items()); print("BUNDLE SHA256 PASS")'
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 "$r2_root/venv/bin/python" r2_validation/observer.py control --data-dir "$r2_root/state"
~~~

Проверить FUNCTIONAL_CHECK.status=PASS, 24 smoke accounts, только CONTROL_ONLY, нулевые prospective execution. До будущего старта сохранить и опубликовать снимок preflight/canary. Системный clock должен быть синхронизирован; сам observer NTP не настраивает.

Шаблон deployment/r2-paper.service.example помещается только в новый user-unit после отдельного разрешения. Заменить REPLACE_WITH_PUBLISHED_FUTURE_UTC на ровно опубликованный start, остальные пути соответствуют командам выше. Проверить ограничения, а не предполагать их применение:

~~~sh
systemd-analyze --user verify "$HOME/.config/systemd/user/r2-paper.service"
systemctl --user daemon-reload
# Enable/start only after explicit deployment permission, preflight PASS and code/protocol publication:
systemctl --user enable --now r2-paper.service
systemctl --user show r2-paper.service -p MainPID -p ActiveState -p CPUQuotaPerSecUSec -p MemoryHigh -p MemoryMax -p TasksMax
cat "$HOME/cryptolab-r2/state/HEARTBEAT.json"
journalctl --user -u r2-paper.service --since '10 minutes ago' --no-pager
~~~

Проверить две обновляющиеся heartbeat, один PID, source/code hashes, отсутствие пропусков, использование ресурсов, реальный restart и идемпотентность. Только после этого и публикации фактических свидетельств возможен статус DEPLOYED_VERIFIED. Постоянную доступность не объявлять по двум heartbeat: требуется наблюдение uptime/пропусков. В данном коммите этот этап не выполнен.

Остановка/откат после разрешённого будущего развёртывания:

~~~sh
systemctl --user disable --now r2-paper.service
# Keep app/state, raw receipts and database for audit. Do not delete or touch other services.
~~~

Если start пропущен, настройки/книги неполны или потеряны данные — фиксировать BLOCKED/пропуски, не заполнять выгодными предположениями. Разрешение paper никогда не является разрешением реальной торговли.
