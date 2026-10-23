#!/usr/bin/env bash
set -e

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "=== Обновление Bybit Multi-Alpha Bot v3.1 на VPS ==="
echo "Рабочая директория: $APP_DIR"

cd "$APP_DIR"

# 1. Получение последних обновлений из Git (если не обновлено)
echo "[1/4] Проверка обновлений Git..."
git fetch origin main
git reset --hard origin/main

# 2. Обновление зависимостей в venv
echo "[2/4] Проверка виртуального окружения Python..."
if [ ! -d "$APP_DIR/venv" ]; then
    python3 -m venv "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/pip" install --upgrade pip -q
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q

# 3. Настройка и перезапуск службы systemd
echo "[3/4] Регистрация/перезапуск службы bybit-bot-v3.service..."
bash "$APP_DIR/v3/setup_v3.sh"

# 4. Проверка статуса
echo "[4/4] Проверка статуса службы..."
sleep 2
systemctl status bybit-bot-v3 --no-pager -n 15

SERVER_IP=$(curl -s -4 ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
echo ""
echo "=================================================="
echo "✅ БОТ v3.1 УСПЕШНО ОБНОВЛЕН И ЗАПУЩЕН!"
echo "🌐 Панель мониторинга v3.1: http://${SERVER_IP}:8082"
echo "📊 API статуса:             http://${SERVER_IP}:8082/api/status"
echo "🪙 Скринер монет:           http://${SERVER_IP}:8082/api/universe"
echo "=================================================="
