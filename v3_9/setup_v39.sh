#!/usr/bin/env bash
set -e

echo "=================================================="
echo "   Установка Bybit Multi-Alpha v3.9 NewDragon Scalp Lab"
echo "   (15m/M1 L2 Microstructure // Z_OFI Scratch // Micro-Router)"
echo "   (Порт 8092 // Inside-Spread Maker Priority)"
echo "=================================================="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
echo "Корневая директория: $APP_DIR"
echo "Директория v3.9: $SCRIPT_DIR"

mkdir -p "$SCRIPT_DIR/reports"

# Регистрация systemd службы bybit-bot-v39.service
echo "[1/3] Регистрация автозапуска systemd (bybit-bot-v39.service)..."
cat << EOF_SERVICE > /etc/systemd/system/bybit-bot-v39.service
[Unit]
Description=Bybit Multi-Alpha v3.9 NewDragon Scalp Lab (Port 8092)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/python $SCRIPT_DIR/web_dashboard.py
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=$APP_DIR
Environment=PORT_V39=8092
Environment=PAPER_TRADING=true
Environment=EQUITY_USDT=300.0

[Install]
WantedBy=multi-user.target
EOF_SERVICE

systemctl daemon-reload
systemctl enable bybit-bot-v39.service
systemctl restart bybit-bot-v39.service

# Открытие порта 8092 в фаерволе
if command -v ufw &> /dev/null; then
    ufw allow 8092/tcp > /dev/null 2>&1 || true
fi

echo "[2/3] Проверка запуска демона v3.9..."
sleep 3

if systemctl is-active --quiet bybit-bot-v39; then
    SERVER_IP=$(curl -s -4 ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
    echo "=================================================="
    echo "  [OK] Демон v3.9 NewDragon Scalp Lab успешно запущен!"
    echo "  Порт: 8092"
    echo "  Веб-дашборд: http://${SERVER_IP}:8092"
    echo "  API статуса: http://${SERVER_IP}:8092/api/status"
    echo "=================================================="
else
    echo "[!] Ошибка: сервис bybit-bot-v39 не запустился. Логи:"
    journalctl -u bybit-bot-v39 -n 25 --no-pager
fi
