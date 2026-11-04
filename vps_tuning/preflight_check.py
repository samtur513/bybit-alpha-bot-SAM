"""
Диагностический скрипт предпусковой проверки сетевого контура VPS:
1. Замер RTT WebSocket ping/pong к Bybit V5 Linear (p50, p95, p99, джиттер).
2. Проверка реальных комиссионных ставок аккаунта через REST API (если заданы ключи).
"""

import asyncio
import os
import statistics
import time
import aiohttp
try:
    import orjson
except ImportError:
    import json

    class _OrjsonFallback:
        @staticmethod
        def dumps(obj, *args, **kwargs) -> bytes:
            return json.dumps(obj).encode("utf-8")

        @staticmethod
        def loads(obj, *args, **kwargs):
            if isinstance(obj, (bytes, bytearray)):
                return json.loads(obj.decode("utf-8"))
            return json.loads(obj)

    orjson = _OrjsonFallback()
import websockets

try:
    import uvloop
    uvloop.install()
except ImportError:
    pass

WS_URL = "wss://stream.bybit.com/v5/public/linear"
REST_URL = "https://api.bybit.com"


async def measure_ws_rtt(samples: int = 50) -> dict:
    rtts = []
    print(f"\n[1/2] Замер RTT WebSocket ({samples} пакетов к {WS_URL})...")
    async with websockets.connect(WS_URL, ping_interval=None) as ws:
        for i in range(samples):
            t0 = time.perf_counter()
            await ws.send(orjson.dumps({"op": "ping"}).decode("utf-8"))
            resp = orjson.loads(await ws.recv())
            t1 = time.perf_counter()
            if resp.get("op") in ("pong", "ping") or resp.get("ret_msg") == "pong":
                rtts.append((t1 - t0) * 1000.0)
            await asyncio.sleep(0.05)

    p50 = statistics.median(rtts)
    sorted_rtts = sorted(rtts)
    p95 = sorted_rtts[int(len(sorted_rtts) * 0.95)]
    p99 = sorted_rtts[int(len(sorted_rtts) * 0.99)]
    jitter = statistics.stdev(rtts) if len(rtts) > 1 else 0.0

    print("--------------------------------------------------")
    print(f"  Min RTT    : {min(rtts):.2f} ms")
    print(f"  Median p50 : {p50:.2f} ms")
    print(f"  95th %     : {p95:.2f} ms")
    print(f"  99th %     : {p99:.2f} ms")
    print(f"  Jitter     : {jitter:.2f} ms")
    print("--------------------------------------------------")

    if p50 <= 10.0 and jitter <= 3.0:
        verdict = "ОТЛИЧНО: Сетевой контур идеален для Post-Only скальпинга и Lead-Lag (< 10 ms)."
    elif p50 <= 25.0:
        verdict = "ХОРОШО: Контур пригоден для свип-скальпинга (E2_SWEEP), но для Lead-Lag пинг пограничный."
    else:
        verdict = "ВНИМАНИЕ: Повышенный пинг/джиттер. Проверьте настройки sysctl и локацию VPS."

    print(f"ВЕРДИКТ: {verdict}\n")
    return {"min": min(rtts), "p50": p50, "p95": p95, "p99": p99, "jitter": jitter}


async def check_fee_rates() -> None:
    api_key = os.getenv("BYBIT_API_KEY", "")
    api_secret = os.getenv("BYBIT_API_SECRET", "")
    if not api_key or not api_secret:
        print("[2/2] Проверка комиссий: API ключи не заданы в .env. Пропуск проверки /v5/account/fee-rate.")
        return

    import hashlib
    import hmac

    print("[2/2] Запрос фактических ставок комиссий через Bybit V5 REST API...")
    now_ms = str(int(time.time() * 1000))
    recv_window = "5000"
    params = "category=linear&symbol=BTCUSDT"
    sign_payload = f"{now_ms}{api_key}{recv_window}{params}"
    signature = hmac.new(api_secret.encode("utf-8"), sign_payload.encode("utf-8"), hashlib.sha256).hexdigest()

    headers = {
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-TIMESTAMP": now_ms,
        "X-BAPI-RECV-WINDOW": recv_window,
        "X-BAPI-SIGN": signature,
    }

    url = f"{REST_URL}/v5/account/fee-rate?{params}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            data = orjson.loads(await resp.read())
            if data.get("retCode") == 0:
                item = data.get("result", {}).get("list", [{}])[0]
                maker = float(item.get("makerFeeRate", 0.0))
                taker = float(item.get("takerFeeRate", 0.0))
                print(f"  Символ        : {item.get('symbol')}")
                print(f"  Maker ставка : {maker * 100:.4f}%")
                print(f"  Taker ставка : {taker * 100:.4f}%")
                if maker <= 0.000325:
                    print("  [OK] Скидка 10% с MNT активна (Maker ~0.0324%)!")
                else:
                    print("  [ВНИМАНИЕ] Ставка Maker стандартная (0.0360%). Включите тумблер MNT в UTA.")
            else:
                print(f"  Ошибка запроса комиссий: {data}")


async def main():
    await measure_ws_rtt(50)
    await check_fee_rates()


if __name__ == "__main__":
    asyncio.run(main())
