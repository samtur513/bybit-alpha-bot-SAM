# R2-RESOURCE-OBSERVABILITY-001
## Диагностический план и анализ инцидента READINESS_RESOURCE_LIMITS в CryptoLab R2

**Статус:** ACCEPTED / READY FOR OWNER REVIEW  
**Инцидент зафиксирован:** `2026-11-16T10:47:35Z` (VPS `150.241.98.70`)  
**Служба:** `bybit-shadow-r2.service`  
**Исключение:** `RuntimeError: READINESS_RESOURCE_LIMITS` (`NRestarts=1799`)  
**Официальная классификация инцидента:** `TRIGGER_NOT_IDENTIFIED`

---

### 1. Архитектурный контекст и первопричина неидентифицируемости

В модуле `readiness.py` службы CryptoLab R2 реализована функция `check_resources(sample)`, проверяющая готовность операционной системы перед каждым циклом оркестрации. При нарушении хотя бы одного лимита вызывается:
```python
raise RuntimeError("READINESS_RESOURCE_LIMITS")
```
**Критический дефект наблюдаемости:** исходная реализация не регистрирует в журнале (stdout/stderr/syslog):
1. Какая именно метрика из 7 порогов вышла за пределы допустимого диапазона;
2. Фактическое числовое значение метрики на момент проверки;
3. Пороговое значение, с которым производилось сравнение.

В результате панель мониторинга 8085 и независимый аудит обоснованно фиксируют статус:
```
READINESS_RESOURCE_LIMITS: TRIGGER_NOT_IDENTIFIED
```
Запрещено домысливать конкретную причину без прямого подтверждения в логах.

---

### 2. Спецификация 7 системных порогов `check_resources()`

При исследовании исходного кода `readiness.py` выявлены 7 строгих пороговых условий:

| № | Контролируемый ресурс | Условие отказа (`raise`) | Физический смысл | Норма для VPS (1 vCPU, 2GB RAM) |
| :--- | :--- | :--- | :--- | :--- |
| **1** | **Free Disk Space** | `free_disk_bytes < needed` (~10.5 GB) | Свободное место на корневом разделе `/` | ~17 GB доступно (запас 6.5 GB) |
| **2** | **Available RAM** | `mem_available_bytes < 512 * 1024**2` | Доступная оперативная память (без swap) | 982 MB доступно (порог 512 MB) |
| **3** | **Free Inodes** | `free_inodes < 4096` | Свободные индексные дескрипторы ФС | > 1,800,000 доступно (порог 4,096) |
| **4** | **File Descriptors** | `fd_soft_limit < 1024` | Лимит открытых дескрипторов процесса (`RLIMIT_NOFILE`) | Стандартное значение systemd: 1024..65536 |
| **5** | **CPU Load Average** | `os.getloadavg()[0] / cpu_count > 0.80` | Средняя загрузка за 1 мин на ядро CPU | Норма 0.10–0.25; пик при компиляции/бэкапе > 0.80 |
| **6** | **Runtime File Count** | `runtime_files > 3000` | Число временных файлов в директории рантайма | В норме < 50 файлов |
| **7** | **Runtime Disk Usage**| `runtime_bytes > 10 * 1024**3` (10 GB) | Суммарный объём рабочего каталога R2 | В норме < 500 MB |

---

### 3. Вероятные факторы риска на конфигурации 1 vCPU / 2GB RAM

На сервере с 1 виртуальным ядром и 2 ГБ ОЗУ наиболее чувствительными к переходным процессам являются:

1. **`load_per_cpu > 0.80`**:
   - При 1 vCPU значение `loadavg[0]` свыше `0.80` означает 80% загрузку процессора.
   - Любая фоновая активность (периодический крон, ротация логов `logrotate`, параллельный запуск проверки `git status`, сборка SQLite WAL) кратковременно поднимает `loadavg` выше `0.80` на 30–90 секунд.
   - В этот момент `check_resources` мгновенно прерывает цикл и завершает процесс, инициируя автоперезапуск systemd.

2. **`mem_available_bytes < 512 MB`**:
   - Общий объём RAM сервера: 1.9 GiB (~1950 MB).
   - При активных 4 процессах флота + v3.2 + Xray VPN использование памяти составляет ~985 MB.
   - В моменты буферизации дискового ввода-вывода или пикового потребления ядром Linux доступная память (`available`) может кратковременно опускаться ниже 512 MB.

---

### 4. Предложение по неинвазивному улучшению наблюдаемости (Proposed Patch)

Для однозначной фиксации сбоя в будущем предлагается модернизировать `check_resources()` в `readiness.py` с сохранением обратной совместимости:

```python
# Предлагаемый фрагмент для будущего обновления readiness.py
def check_resources(sample: Dict[str, Any]) -> None:
    # 1. Disk
    free_disk = shutil.disk_usage("/").free
    if free_disk < needed_disk_bytes:
        logger.error(
            "RESOURCE_LIMIT_BREACH: metric=free_disk, val_gb=%.2f, limit_gb=%.2f",
            free_disk / (1024**3), needed_disk_bytes / (1024**3)
        )
        raise RuntimeError(f"READINESS_RESOURCE_LIMITS: free_disk={free_disk} < {needed_disk_bytes}")

    # 2. RAM
    mem = psutil.virtual_memory() if 'psutil' in sys.modules else None
    # fallback via /proc/meminfo
    if mem_available < 512 * 1024 * 1024:
        logger.error(
            "RESOURCE_LIMIT_BREACH: metric=mem_available, val_mb=%.1f, limit_mb=512.0",
            mem_available / (1024**2)
        )
        raise RuntimeError(f"READINESS_RESOURCE_LIMITS: mem_available={mem_available} < 512MB")

    # 3. CPU Load
    load_1m = os.getloadavg()[0]
    cpus = os.cpu_count() or 1
    load_ratio = load_1m / cpus
    if load_ratio > 0.80:
        logger.error(
            "RESOURCE_LIMIT_BREACH: metric=cpu_load_per_core, val=%.3f, limit=0.800 (load_1m=%.2f, cpus=%d)",
            load_ratio, load_1m, cpus
        )
        raise RuntimeError(f"READINESS_RESOURCE_LIMITS: load_ratio={load_ratio:.3f} > 0.80")
```

---

### 5. Безопасные команды Termius для мониторинга 7 порогов в реальном времени

Владелец сервера может в любой момент выполнить следующий безопасный read-only скрипт через Termius для мгновенного аудита всех 7 параметров:

```bash
/opt/bybit-bot/venv/bin/python -c "
import os, sys, shutil, resource

print('=== CRYPTOLAB R2 RESOURCE LIMITS AUDIT ===')

# 1. Disk Free
du = shutil.disk_usage('/')
free_gb = du.free / (1024**3)
print(f'1. Free Disk: {free_gb:.2f} GB (Порог: ~10.5 GB) ->', 'OK' if free_gb >= 10.5 else 'FAIL')

# 2. Available RAM
mem_avail_mb = 0
with open('/proc/meminfo', 'r') as f:
    for line in f:
        if line.startswith('MemAvailable:'):
            mem_avail_mb = int(line.split()[1]) / 1024
print(f'2. Available RAM: {mem_avail_mb:.1f} MB (Порог: 512.0 MB) ->', 'OK' if mem_avail_mb >= 512 else 'FAIL')

# 3. Free Inodes
st = os.statvfs('/')
free_inodes = st.f_favail
print(f'3. Free Inodes: {free_inodes} (Порог: 4096) ->', 'OK' if free_inodes >= 4096 else 'FAIL')

# 4. FD Soft Limit
soft_fd, hard_fd = resource.getrlimit(resource.RLIMIT_NOFILE)
print(f'4. FD Soft Limit: {soft_fd} (Порог: 1024) ->', 'OK' if soft_fd >= 1024 else 'FAIL')

# 5. CPU Load per Core
load_1m = os.getloadavg()[0]
cpus = os.cpu_count() or 1
load_per_cpu = load_1m / cpus
print(f'5. CPU Load/Core: {load_per_cpu:.3f} (load_1m={load_1m:.2f}, cpus={cpus}, Порог: 0.80) ->', 'OK' if load_per_cpu <= 0.80 else 'FAIL')

# 6. Runtime Files
r2_dir = '/opt/bybit-shadow/CryptoLab/tmp' if os.path.exists('/opt/bybit-shadow/CryptoLab/tmp') else '/tmp'
rt_files = len(os.listdir(r2_dir))
print(f'6. Runtime Files in {r2_dir}: {rt_files} (Порог: 3000) ->', 'OK' if rt_files <= 3000 else 'FAIL')

# 7. Runtime Bytes
rt_bytes_mb = sum(os.path.getsize(os.path.join(r2_dir, f)) for f in os.listdir(r2_dir) if os.path.isfile(os.path.join(r2_dir, f))) / (1024**2)
print(f'7. Runtime Size: {rt_bytes_mb:.2f} MB (Порог: 10240 MB) ->', 'OK' if rt_bytes_mb <= 10240 else 'FAIL')

print('==========================================')
"
```
