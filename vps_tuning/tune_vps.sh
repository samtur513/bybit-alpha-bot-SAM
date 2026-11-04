#!/usr/bin/env bash
# ==============================================================================
# Скрипт низколатентного тюнинга сервера Ubuntu / Debian под Bybit HFT Scalper
# Применяет настройки сетевого стека Linux, CPU governor и системные лимиты
# ==============================================================================

set -e

if [ "$EUID" -ne 0 ]; then
  echo "[ОШИБКА] Запустите скрипт с правами root: sudo bash tune_vps.sh"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== [1/5] Применение параметров сетевого стека sysctl ==="
cp "${SCRIPT_DIR}/sysctl_lowlatency.conf" /etc/sysctl.d/99-bybit-lowlatency.conf
sysctl --system > /dev/null
echo "[OK] sysctl применен успешно (/etc/sysctl.d/99-bybit-lowlatency.conf)."

echo "=== [2/5] Настройка режима CPU (Performance Governor) ==="
if command -v cpupower &> /dev/null; then
    cpupower frequency-set -g performance || true
    echo "[OK] cpupower переведен в режим performance."
elif [ -d "/sys/devices/system/cpu/cpu0/cpufreq" ]; then
    for gov in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
        echo "performance" > "$gov" 2>/dev/null || true
    done
    echo "[OK] CPU scaling_governor выставлен в performance."
else
    echo "[ИНФО] Управление частотой CPU недоступно (возможно, VPS в гипервизоре KVM без прямого управления CPU)."
fi

echo "=== [3/5] Увеличение лимитов файловых дескрипторов (nofile) ==="
cat << 'EOF' > /etc/security/limits.d/99-hft-limits.conf
* soft nofile 65535
* hard nofile 65535
root soft nofile 65535
root hard nofile 65535
EOF
echo "[OK] Лимиты дескрипторов обновлены (65535)."

echo "=== [4/5] Установка Python зависимостей для диагностики ==="
python3 -m pip install -q orjson websockets aiohttp || true

echo "=== [5/5] Запуск предпусковой диагностики WebSocket RTT ==="
python3 "${SCRIPT_DIR}/preflight_check.py" || true

echo "=== Низколатентный тюнинг сервера успешно завершен! ==="
