"""
Диагностический скрипт предпусковой проверки сетевого контура VPS (Zero-Dependency / Pure Python Stdio/Stdlib):
1. Замер TCP RTT и TLS Handshake к Bybit Matching/Stream серверам.
2. Замер RTT WebSocket ping/pong к Bybit V5 Linear (p50, p95, p99, джиттер).
3. Проверка реальных комиссионных ставок аккаунта через REST API (если заданы ключи в .env).

Не требует сторонних библиотек (aiohttp, websockets, requests). Работает на чистом Python 3.8+!
"""

import base64
import hashlib
import hmac
import json
import os
import socket
import ssl
import statistics
import time
import urllib.request
import urllib.error

WS_HOST = "stream.bybit.com"
WS_PORT = 443
WS_PATH = "/v5/public/linear"
REST_URL = "https://api.bybit.com"


def _recv_exact(sock: ssl.SSLSocket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionResetError("Соединение закрыто удалённым сервером")
        buf.extend(chunk)
    return bytes(buf)


def _read_ws_frame(sock: ssl.SSLSocket) -> tuple[int, bytes]:
    header = _recv_exact(sock, 2)
    b1, b2 = header[0], header[1]
    opcode = b1 & 0x0F
    is_masked = bool(b2 & 0x80)
    plen = b2 & 0x7F

    if plen == 126:
        plen = int.from_bytes(_recv_exact(sock, 2), "big")
    elif plen == 127:
        plen = int.from_bytes(_recv_exact(sock, 8), "big")

    if is_masked:
        mask_key = _recv_exact(sock, 4)
        raw = bytearray(_recv_exact(sock, plen))
        for i in range(len(raw)):
            raw[i] ^= mask_key[i % 4]
        return opcode, bytes(raw)
    return opcode, _recv_exact(sock, plen)


def _send_ws_text_frame(sock: ssl.SSLSocket, text: str) -> None:
    data = text.encode("utf-8")
    length = len(data)
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))

    if length <= 125:
        header = bytes([0x81, 0x80 | length])
    elif length <= 65535:
        header = bytes([0x81, 0x80 | 126]) + length.to_bytes(2, "big")
    else:
        header = bytes([0x81, 0x80 | 127]) + length.to_bytes(8, "big")

    sock.sendall(header + mask + masked)


def measure_ws_rtt(samples: int = 50) -> dict:
    print(f"\n==================================================")
    print(f" [1/2] Замер сетевых задержек (RTT) к Bybit V5 WS")
    print(f" Сервер: wss://{WS_HOST}{WS_PATH} ({samples} пакетов)")
    print(f"==================================================")

    # 1. TCP Handshake measurement
    t_tcp_start = time.perf_counter()
    raw_sock = socket.create_connection((WS_HOST, WS_PORT), timeout=7.0)
    # TCP_NODELAY disable Nagle for measurement
    raw_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    tcp_rtt_ms = (time.perf_counter() - t_tcp_start) * 1000.0

    # 2. TLS Handshake measurement
    t_tls_start = time.perf_counter()
    ctx = ssl.create_default_context()
    ws_sock = ctx.wrap_socket(raw_sock, server_hostname=WS_HOST)
    tls_handshake_ms = (time.perf_counter() - t_tls_start) * 1000.0

    print(f"  TCP Connect RTT : {tcp_rtt_ms:.2f} ms")
    print(f"  TLS Handshake   : {tls_handshake_ms:.2f} ms")

    # 3. HTTP Upgrade to WebSocket
    sec_key = base64.b64encode(os.urandom(16)).decode("ascii")
    upgrade_req = (
        f"GET {WS_PATH} HTTP/1.1\r\n"
        f"Host: {WS_HOST}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {sec_key}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    )
    ws_sock.sendall(upgrade_req.encode("utf-8"))

    hdr_buf = b""
    while b"\r\n\r\n" not in hdr_buf:
        chunk = ws_sock.recv(1024)
        if not chunk:
            raise ConnectionResetError("Сервер закрыл соединение до завершения рукопожатия WebSocket")
        hdr_buf += chunk

    status_line = hdr_buf.split(b"\r\n")[0].decode("utf-8", errors="replace")
    if "101" not in status_line:
        print(f"  Ошибка WebSocket Handshake: {status_line}")
        ws_sock.close()
        return {}

    print(f"  WS Handshake    : {status_line}")
    print(f"  Замер {samples} ping/pong циклов: ", end="", flush=True)

    ping_payload = json.dumps({"op": "ping"})
    rtts = []

    try:
        for i in range(samples):
            t0 = time.perf_counter()
            _send_ws_text_frame(ws_sock, ping_payload)
            opcode, resp_data = _read_ws_frame(ws_sock)
            t1 = time.perf_counter()

            rtt = (t1 - t0) * 1000.0
            rtts.append(rtt)
            if (i + 1) % 10 == 0:
                print(f"[{i + 1}/{samples}]", end="", flush=True)
            elif (i + 1) % 2 == 0:
                print(".", end="", flush=True)
            time.sleep(0.04)
        print(" готово!")
    finally:
        try:
            ws_sock.close()
        except Exception:
            pass

    if not rtts:
        print("  [ОШИБКА] Не удалось получить ни одного pong-ответа")
        return {}

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
        verdict = "ИТОГ: ИДЕАЛЬНО (Токио / AWS ap-northeast-1). Допустимы все HFT-стратегии (E1, E2, E3)."
    elif p50 <= 35.0:
        verdict = "ИТОГ: ХОРОШО. Пригоден для E2_SWEEP (свип ликвидности). Для микросекундного Lead-Lag желателен Токио."
    else:
        verdict = f"ИТОГ: УДАЛЁННЫЙ РЕГИОН (Европа ~{p50:.0f} ms). Пригоден для свипа 15m/1h и свинг-пробоев v3.0-v3.6. Для HFT L2 (<10ms) требуется VPS в Токио."

    print(f"ВЕРДИКТ: {verdict}\n")
    return {"min": min(rtts), "p50": p50, "p95": p95, "p99": p99, "jitter": jitter, "tcp": tcp_rtt_ms}


def check_fee_rates() -> None:
    print(f"==================================================")
    print(f" [2/2] Аудит комиссионных ставок Bybit V5 Account")
    print(f"==================================================")

    api_key = os.getenv("BYBIT_API_KEY", "").strip()
    api_secret = os.getenv("BYBIT_API_SECRET", "").strip()

    # Поиск в файлах .env
    for candidate in [".env", "/opt/bybit-bot/.env", os.path.expanduser("~/.env")]:
        if (not api_key or not api_secret) and os.path.exists(candidate):
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("BYBIT_API_KEY="):
                            api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                        elif line.startswith("BYBIT_API_SECRET="):
                            api_secret = line.split("=", 1)[1].strip().strip('"').strip("'")
            except Exception:
                pass

    if not api_key or not api_secret:
        print("  API ключи не обнаружены в переменных окружения или .env.")
        print("  Для проверки ставок Maker/Taker задайте BYBIT_API_KEY и BYBIT_API_SECRET.")
        print("==================================================\n")
        return

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
        "User-Agent": "BybitPreflight/1.0",
    }

    url = f"{REST_URL}/v5/account/fee-rate?{params}"
    req = urllib.request.Request(url, headers=headers, method="GET")

    try:
        with urllib.request.urlopen(req, timeout=7.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("retCode") == 0:
                item = data.get("result", {}).get("list", [{}])[0]
                maker = float(item.get("makerFeeRate", 0.0))
                taker = float(item.get("takerFeeRate", 0.0))
                print(f"  Символ        : {item.get('symbol')}")
                print(f"  Maker ставка : {maker * 100:.4f}%")
                print(f"  Taker ставка : {taker * 100:.4f}%")
                if maker <= 0.000325:
                    print("  [OK] Скидка 10% с MNT АКТИВНА (Maker ~0.0324%)!")
                else:
                    print("  [ВНИМАНИЕ] Ставка Maker стандартная (0.0360%). Рекомендуется включить MNT в UTA.")
            else:
                print(f"  Ошибка Bybit API: retCode={data.get('retCode')}, retMsg={data.get('retMsg')}")
    except urllib.error.HTTPError as e:
        print(f"  HTTP ошибка {e.code}: {e.read().decode('utf-8', errors='replace')}")
    except Exception as e:
        print(f"  Ошибка соединения при запросе комиссий: {e}")

    print("==================================================\n")


def main():
    measure_ws_rtt(50)
    check_fee_rates()


if __name__ == "__main__":
    main()
