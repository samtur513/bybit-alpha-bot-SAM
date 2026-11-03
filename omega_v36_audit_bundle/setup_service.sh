#!/usr/bin/env bash
set -e

echo "=================================================="
echo "   Установка Bybit Multi-Alpha Bot v3.6 Omega     "
echo "   (Omega Dual-Regime ZF-8 + News Core v1.2)      "
echo "   (Порт 8089 // Trend + IRAS Maker + News Guard) "
echo "=================================================="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "Директория установки: $SCRIPT_DIR"

# 1. Системные пакеты
echo "[1/4] Проверка системных пакетов..."
if command -v apt-get &> /dev/null; then
    apt-get update -qq
    apt-get install -y -qq python3-venv python3-pip curl ufw > /dev/null 2>&1 || true
fi

# 2. Виртуальное окружение Python
echo "[2/4] Проверка виртуального окружения Python..."
mkdir -p "$SCRIPT_DIR/reports"
if [ ! -d "$SCRIPT_DIR/venv" ]; then
    python3 -m venv "$SCRIPT_DIR/venv"
fi
"$SCRIPT_DIR/venv/bin/pip" install --upgrade pip -qq
"$SCRIPT_DIR/venv/bin/pip" install -r "$SCRIPT_DIR/requirements.txt" -qq

# 3. Регистрация systemd службы bybit-bot-v36.service
echo "[3/4] Регистрация автозапуска systemd (bybit-bot-v36.service)..."
cat << EOF_SERVICE > /etc/systemd/system/bybit-bot-v36.service
[Unit]
Description=Bybit Multi-Alpha Omega Dual-Regime ZF-8 Engine v3.6 1h (Port 8089)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$SCRIPT_DIR
ExecStart=$SCRIPT_DIR/venv/bin/python $SCRIPT_DIR/web_dashboard.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=$SCRIPT_DIR

[Install]
WantedBy=multi-user.target
EOF_SERVICE

systemctl daemon-reload
systemctl enable bybit-bot-v36.service
systemctl restart bybit-bot-v36.service

# Открытие порта 8089 в фаерволе
if command -v ufw &> /dev/null; then
    ufw allow 8089/tcp > /dev/null 2>&1 || true
fi

# 4. Проверка запуска
echo "[4/4] Проверка запуска демона v3.6..."
sleep 3

if systemctl is-active --quiet bybit-bot-v36; then
    echo "=================================================="
    echo "  [OK] Демон v3.6 Omega Dual-Regime ZF-8 успешно запущен!"
    echo "  Порт: 8089"
    echo "  Веб-дашборд: http://$(curl -s ifconfig.me || echo 'localhost'):8089"
    echo "=================================================="
else
    echo "[!] Ошибка: сервис bybit-bot-v36 не запустился. Логи:"
    journalctl -u bybit-bot-v36 -n 20 --no-pager
    exit 1
fi
