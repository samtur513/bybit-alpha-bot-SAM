#!/usr/bin/env bash
set -e

echo "=================================================="
echo "   Установка Bybit Multi-Alpha v3.14 Basket Dispersion"
echo "   (Synthetic 50% BTC + 50% ETH Benchmark // Funding Carry)"
echo "   (Порт 8094 // 2-Stage TP + 4h KER Filter)"
echo "=================================================="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
echo "Корневая директория: $APP_DIR"
echo "Директория v3.14: $SCRIPT_DIR"

mkdir -p "$SCRIPT_DIR/reports"

# Регистрация systemd службы bybit-dispersion-8094.service
echo "[1/3] Регистрация автозапуска systemd (bybit-dispersion-8094.service)..."
cat << EOF_SERVICE > /etc/systemd/system/bybit-dispersion-8094.service
[Unit]
Description=Bybit Multi-Alpha v3.14 Synthetic Basket Dispersion & Funding Carry (Port 8094)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/python $SCRIPT_DIR/src/dispersion_8094.py
Restart=always
RestartSec=3
EnvironmentFile=-$APP_DIR/.env
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=$APP_DIR
Environment=PORT_8094=8094
Environment=PAPER_TRADING=true
Environment=EQUITY_USDT=300.0
Environment=TCA_DB_PATH=$APP_DIR/v3_10_sweep/tca_attribution.db

[Install]
WantedBy=multi-user.target
EOF_SERVICE

systemctl daemon-reload
systemctl enable bybit-dispersion-8094.service
systemctl restart bybit-dispersion-8094.service

# Открытие порта 8094 в фаерволе
if command -v ufw &> /dev/null; then
    ufw allow 8094/tcp > /dev/null 2>&1 || true
fi

echo "[2/3] Проверка запуска демона v3.14..."
sleep 2
systemctl status bybit-dispersion-8094.service --no-pager | grep "Active:"
echo "[3/3] v3.14 Dispersion Engine онлайн на порту 8094!"
