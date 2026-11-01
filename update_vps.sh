#!/usr/bin/env bash
set -e

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "=== Обновление Bybit Multi-Alpha Bot Suite (v2.0, v3.1 Pure, v3.1 News) на VPS ==="
echo "Рабочая директория: $APP_DIR"

cd "$APP_DIR"

# 1. Сброс локальных измененных файлов состояния и получение обновлений Git
echo "[1/4] Проверка обновлений Git (сброс к origin/main)..."
git fetch origin main
git reset --hard origin/main

# 2. Обновление зависимостей в venv
echo "[2/4] Проверка виртуального окружения Python..."
if [ ! -d "$APP_DIR/venv" ]; then
    python3 -m venv "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/pip" install --upgrade pip -q
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q

# 3. Настройка и перезапуск всех трех служб systemd
echo "[3/4] Регистрация/обновление служб systemd..."
bash "$APP_DIR/v2_1/setup_v21.sh"
bash "$APP_DIR/v3/setup_v3.sh"

systemctl daemon-reload
systemctl restart bybit-bot bybit-bot-v21 bybit-bot-v3

# 4. Проверка статуса
echo "[4/4] Проверка статуса всех 3 служб..."
sleep 2
echo "--- bybit-bot (Port 8080) ---"
systemctl is-active bybit-bot || true
echo "--- bybit-bot-v21 (Port 8081) ---"
systemctl is-active bybit-bot-v21 || true
echo "--- bybit-bot-v3 (Port 8082) ---"
systemctl is-active bybit-bot-v3 || true

SERVER_IP=$(curl -s -4 ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
echo ""
echo "================================================================="
echo "✅ ВСЕ 3 ВЕРСИИ УСПЕШНО ОБНОВЛЕНЫ И ЗАПУЩЕНЫ!"
echo "🌐 v2.0 Baseline (Port 8080):              http://${SERVER_IP}:8080"
echo "🌐 v3.1 Pure Quant - No News (Port 8081):   http://${SERVER_IP}:8081"
echo "🌐 v3.1 Full Quant + News Core (Port 8082): http://${SERVER_IP}:8082"
echo ""
echo "💡 Для синхронного обнуления всех трех ботов до $300.00 запустите:"
echo "   bash reset_all_to_300.sh"
echo "================================================================="
