"""
CLI Security Audit & Verification Tool for Bybit Live Execution Gate.
Run on VPS: python v3/verify_live_credentials.py
"""

from decimal import Decimal
import os
from pathlib import Path
import sys

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Load .env file safely
env_path = REPO_ROOT / ".env"
if env_path.exists():
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip().strip("'\"")

from v3.src.live_execution_gate import LiveExecutionGate, SecurityViolationError


def run_security_audit():
    print("=" * 80)
    print("   ПРОВЕРКА БЕЗОПАСНОСТИ БОЕВОГО ПОДКЛЮЧЕНИЯ BYBIT (LIVE EXECUTION GATE)")
    print("=" * 80)

    api_key = os.environ.get("BYBIT_API_KEY", "")
    api_secret = os.environ.get("BYBIT_API_SECRET", "")
    live_enabled = os.environ.get("LIVE_TRADING_ENABLED", "").lower() in ("true", "1")
    testnet = os.environ.get("BYBIT_TESTNET", "").lower() in ("true", "1")

    masked_key = (api_key[:6] + "..." + api_key[-4:]) if len(api_key) > 10 else "(не задан)"
    print(f"Ключ API:              {masked_key}")
    print(f"Режим сети:            {'TESTNET' if testnet else 'MAINNET (Реальная биржа Bybit)'}")
    print(f"Боевой рубильник:      {'🟢 ВКЛЮЧЕН (LIVE_TRADING_ENABLED=true)' if live_enabled else '⚪ DRY-RUN (Безопасная симуляция)'}")
    print(f"Макс. размер позиции:  ${os.environ.get('LIVE_MAX_NOTIONAL_PER_POS', '30.0')} USDT")
    print(f"Макс. сумма портфеля:  ${os.environ.get('LIVE_MAX_PORTFOLIO_NOTIONAL', '60.0')} USDT")
    print(f"Суточный рубильник:    -${os.environ.get('LIVE_MAX_DAILY_LOSS_USD', '10.0')} USDT / {os.environ.get('LIVE_MAX_DAILY_LOSS_PCT', '5.0')}%")
    print("-" * 80)

    if not api_key or not api_secret or "your_subaccount" in api_key:
        print("❌ ОШИБКА: API-ключи не настроены.")
        print(f"   Создайте файл .env на основе шаблона .env.example:")
        print(f"   cp .env.example .env && nano .env")
        print("=" * 80)
        sys.exit(1)

    print("Подключение к Bybit v5 API для аудита разрешений...")
    gate = LiveExecutionGate()

    try:
        report = gate.verify_security_handshake()
    except SecurityViolationError as e:
        print("\n" + "!" * 80)
        print("🚨 КРИТИЧЕСКАЯ УГРОЗА БЕЗОПАСНОСТИ!")
        print(f"   {e}")
        print("   Торговля заблокирована. Зайдите на Bybit и СНИМИТЕ галочку 'Withdrawals'!")
        print("!" * 80 + "\n")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Ошибка сетевого запроса к Bybit: {e}")
        sys.exit(1)

    print("\n=== РЕЗУЛЬТАТЫ АУДИТА РАЗРЕШЕНИЙ BYBIT ===")
    print(f"Тип аккаунта:          {'✅ Субаккаунт (Изолирован)' if report.is_subaccount else '⚠️ Основной аккаунт (Рекомендуется субаккаунт)'}")
    print(f"Торговля деривативами: {'✅ ВКЛЮЧЕНА (ContractTrade активен)' if report.contract_trading_enabled else '❌ ОТКЛЮЧЕНА'}")
    print(f"Вывод средств:         {'✅ ЗАПРЕЩЕН (Безопасно - бот не может вывести деньги)' if report.withdrawals_disabled else '❌ РАЗРЕШЕН (Опасно!)'}")
    print(f"Привязка к IP:         {report.ip_whitelist if report.ip_whitelist else '⚠️ Без привязки к IP (Рекомендуется привязать к VPS IP)'}")
    print(f"Тип кошелька:          {report.wallet_type}")
    print(f"Общий баланс:          ${report.total_equity:.2f} USDT")
    print(f"Доступная маржа:       ${report.available_balance:.2f} USDT")

    if report.warnings:
        print("\nПредупреждения:")
        for w in report.warnings:
            print(f"  • {w}")

    if report.errors:
        print("\nОшибки конфигурации:")
        for err in report.errors:
            print(f"  • {err}")
        print("\n❌ АУДИТ НЕ ПРОЙДЕН. Исправьте ошибки перед запуском.")
        sys.exit(1)

    print("\n" + "=" * 80)
    print("✅ АУДИТ БЕЗОПАСНОСТИ УСПЕШНО ПРОЙДЕН!")
    print("   Ключи проверены, вывод средств заблокирован, баланс подтвержден.")
    print("   Модуль LiveExecutionGate готов к безопасной микро-торговле.")
    print("=" * 80)


if __name__ == "__main__":
    run_security_audit()
