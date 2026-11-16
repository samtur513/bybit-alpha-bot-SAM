"""Regression tests for independently bounded concurrent TORG_BOT HTTP on 8085.

No VPS files, DBs, collectors, or trading services are accessed.
"""
import http.client
import threading
import time
import urllib.error
import urllib.request

from v3_2.src.integrations.three_system_dashboard import (
    BoundedDashboardHTTPServer,
    create_dashboard_server,
)


def start_server(supplier=None):
    server=create_dashboard_server(host="127.0.0.1",port=0,report_supplier=supplier)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    return server,thread


def shutdown(server,thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)
    assert not thread.is_alive()


def test_blocked_report_does_not_block_health_or_second_client():
    report_entered=threading.Event()
    release_report=threading.Event()

    def blocked_report(_handler):
        report_entered.set()
        assert release_report.wait(timeout=5), "test report was not released"
        return {"status":"ok","systems":{}}

    server,thread=start_server(blocked_report)
    port=server.server_address[1]
    slow_response={}
    def slow_client():
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status",timeout=6) as resp:
                slow_response["status"]=resp.status
        except Exception as exc:
            slow_response["error"]=repr(exc)

    worker=threading.Thread(target=slow_client,daemon=True)
    worker.start()
    try:
        assert report_entered.wait(timeout=3), "slow request not accepted"
        start=time.monotonic()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health",timeout=2) as resp:
            assert resp.status==200
            assert b'"status": "ok"' in resp.read()
        assert time.monotonic()-start<1.5, "health incorrectly queued behind slow SQLite/report"
    finally:
        release_report.set()
        worker.join(timeout=6)
        shutdown(server,thread)
    assert slow_response.get("status")==200,slow_response


def test_concurrency_hard_cap_returns_503_and_recovers():
    server,thread=start_server(lambda: {"status":"ok"})
    port=server.server_address[1]
    assert isinstance(server,BoundedDashboardHTTPServer)
    assert server.max_active_requests==8
    assert server.request_queue_size>=8
    assert server.daemon_threads
    try:
        for _ in range(server.max_active_requests):
            assert server._request_slots.acquire(blocking=False)
        try:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/health",timeout=2)
                assert False,"overload must return 503"
            except urllib.error.HTTPError as exc:
                assert exc.code==503
                assert exc.headers.get("Connection","").lower()=="close"
        finally:
            for _ in range(server.max_active_requests):
                server._request_slots.release()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health",timeout=2) as resp:
            assert resp.status==200
    finally:
        shutdown(server,thread)


def test_response_explicitly_closes_connection():
    server,thread=start_server()
    port=server.server_address[1]
    try:
        conn=http.client.HTTPConnection("127.0.0.1",port,timeout=2)
        conn.request("GET","/health")
        resp=conn.getresponse()
        assert resp.status==200
        assert resp.getheader("Content-Length") is not None
        assert resp.getheader("Connection","").lower()=="close"
        resp.read()
        conn.close()
    finally:
        shutdown(server,thread)
