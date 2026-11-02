#!/usr/bin/env bash
set -e

echo "=================================================="
echo "   Установка Bybit Multi-Alpha Meta-Dashboard     "
echo "   (Единая панель мониторинга 5 квантовых движков)"
echo "   (Порт 8085 // WebSocket Bybit + News Core v1.2)"
echo "=================================================="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
echo "Корневая директория проекта: $APP_DIR"
echo "Директория meta_dashboard: $SCRIPT_DIR"

# 1. Системные пакеты
echo "[1/4] Проверка системных пакетов..."
if command -v apt-get &> /dev/null; then
    apt-get update -qq
    apt-get install -y -qq curl ufw > /dev/null 2>&1 || true
fi

# 2. Проверка виртуального окружения Python
echo "[2/4] Проверка окружения Python..."
if [ ! -d "$APP_DIR/venv" ]; then
    python3 -m venv "$APP_DIR/venv"
fi

# 3. Регистрация systemd службы bybit-meta-dashboard.service
echo "[3/4] Регистрация автозапуска systemd (bybit-meta-dashboard.service)..."
cat << EOF_SERVICE > /etc/systemd/system/bybit-meta-dashboard.service
[Unit]
Description=Bybit Multi-Alpha Ecosystem Master Meta-Dashboard (Port 8085)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/python $SCRIPT_DIR/meta_server.py
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=$APP_DIR
Environment=META_PORT=8085
Environment=ENGINE_HOST=127.0.0.1

[Install]
WantedBy=multi-user.target
EOF_SERVICE

systemctl daemon-reload
systemctl enable bybit-meta-dashboard.service
systemctl restart bybit-meta-dashboard.service

# Открытие порта 8085 в фаерволе
if command -v ufw &> /dev/null; then
    ufw allow 8085/tcp > /dev/null 2>&1 || true
fi

# 4. Проверка запуска
echo "[4/4] Проверка запуска Meta-Dashboard..."
sleep 3

if systemctl is-active --quiet bybit-meta-dashboard; then
    SERVER_IP=$(curl -s -4 ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
    echo ""
    echo "=================================================="
    echo "✅ MASTER META-DASHBOARD УСПЕШНО ЗАПУЩЕН!"
    echo "=================================================="
    echo ""
    echo "🌐 Единая панель (Meta-Dashboard): http://${SERVER_IP}:8085"
    echo "📊 API статуса 5 движков:         http://${SERVER_IP}:8085/api/meta_status"
    echo "📈 API графика динамики:          http://${SERVER_IP}:8085/api/meta_chart"
    echo ""
    echo "🔗 Ссылки на персональные панели:"
    echo "   - Port 8080: http://${SERVER_IP}:8080 (v2.0 1h Base)"
    echo "   - Port 8081: http://${SERVER_IP}:8081 (v2.1 4h Static + News)"
    echo "   - Port 8082: http://${SERVER_IP}:8082 (v3.1.1 1h Dynamic + News v1.2)"
    echo "   - Port 8083: http://${SERVER_IP}:8083 (v3.2 Dual-Regime Trend + Range)"
    echo "   - Port 8084: http://${SERVER_IP}:8084 (v3.1.4 4h Champion + News v1.2)"
    echo "=================================================="
else
    echo "❌ Ошибка запуска службы bybit-meta-dashboard.service"
    journalctl -u bybit-meta-dashboard -n 30 --no-pager
    exit 1
fi
