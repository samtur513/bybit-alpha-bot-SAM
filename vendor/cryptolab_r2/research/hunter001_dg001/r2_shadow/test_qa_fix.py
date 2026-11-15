import datetime
import json
import pytest
import runtime
from telemetry import export, PROFILES
UTC = datetime.timezone.utc
DAY = datetime.datetime(2030,1,1,tzinfo=UTC)

def cohort():
    return dict(status="PLANNED",t0_utc=DAY.isoformat(),minimum_end_utc=runtime.add_months(DAY).isoformat(),
        initial_capital_usdt=300,participants=["TORG_V32_NATIVE_NEW_COHORT","TORG_V32_REALISTIC_SHADOW","CRYPTOLAB_R2_SHADOW"])

def prepared(tmp_path):
    return runtime.prepare(tmp_path/"runtime",cohort(),now=DAY-datetime.timedelta(days=1))

def quote_set(now,price=100):
    return [dict(meta=dict(symbol=s),book=dict(s=s,ts=int(now.timestamp()*1000)),mid=price,
        bids=[(price-1,1000)],asks=[(price+1,1000)],book_receipt=dict(available_at=now.isoformat()))
        for s in ["BTCUSDT","ETHUSDT","SOLUSDT"]]

def exported(instance,root,now):
    kwargs={"quotes":instance.quote_cache} if hasattr(instance,"quote_cache") else {}
    return export(root,root/"telemetry.json",now=now,**kwargs)

def test_minute_quote_refresh_between_hourly_marks(tmp_path,monkeypatch):
    root=prepared(tmp_path)
    ob=runtime.frozen_modules()
    clock=[DAY+datetime.timedelta(hours=2)]
    monkeypatch.setattr(ob,"utc",lambda:clock[0])
    monkeypatch.setattr(ob.Observer,"markets",lambda *a,**k:quote_set(clock[0],105))
    instance=runtime.make_observer(root)
    with instance.store.db:
        for key in PROFILES:
            a=dict(instance.store.accounts())[key]
            a.update(cash=200.,qty=[1.,0.,0.])
            instance.store.db.execute("UPDATE accounts SET payload=? WHERE id=?",(json.dumps(a),key))
        instance.mark_accounts("RISK:2030-01-01T02",quote_set(clock[0]),"FORWARD_HOURLY_RISK_MARK",risk_only=True)
    clock[0]+=datetime.timedelta(minutes=3)
    instance.markets(refresh=False,depth=1)
    before=instance.store.db.execute("SELECT * FROM accounts ORDER BY id").fetchall()
    data=exported(instance,root,clock[0])
    assert data["profiles"][0]["equity_usdt"]==305.
    assert data["comparison"]["r2_minus_t01_usdt"]==0.
    assert data["profiles"][0]["valuation_at"]==clock[0].isoformat()
    assert before==instance.store.db.execute("SELECT * FROM accounts ORDER BY id").fetchall()
    instance.store.db.close()

def test_flags_without_readiness_results_fail_before_lock(tmp_path,monkeypatch):
    root=prepared(tmp_path)
    policy=dict(deployment_authorized=True,vps_gate_passed=True,three_participants_ready=True,
        telemetry_contract_accepted=True,local_verification_passed=True,authorized_start_utc=DAY.isoformat())
    path=tmp_path/"policy.json"
    path.write_text(json.dumps(policy))
    ob=runtime.frozen_modules()
    monkeypatch.setattr(ob,"process_lock",lambda *a:pytest.fail("Launch reached lock without readiness evidence"))
    with pytest.raises(RuntimeError,match="READINESS"):
        runtime.run(root,path)

import copy
import hashlib
import io
import socket
import sqlite3
from pathlib import Path
from marks import QuoteCache
import readiness

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*a,**k):pytest.fail("UNEXPECTED_NETWORK")
    monkeypatch.setattr(socket.socket,"connect",deny)
    monkeypatch.setattr(socket,"create_connection",deny)

def ledger(root):
    d=sqlite3.connect((root/"observer.sqlite").as_uri()+"?mode=ro",uri=True)
    try:return {n:d.execute("SELECT * FROM "+n+" ORDER BY 1").fetchall() for n in ["accounts","events","decisions"]}
    finally:d.close()

def test_full_hour_quotes_fresh_without_extra_polls_or_equity_writes(tmp_path,monkeypatch):
    root=prepared(tmp_path)
    ob=runtime.frozen_modules()
    clock=[DAY+datetime.timedelta(hours=2)]
    calls=[]
    def markets(*a,**k):
        calls.append(k)
        rows=quote_set(clock[0],100+clock[0].minute)
        for m in rows:
            r=dict(available_at=clock[0].isoformat(),ingested_at=clock[0].isoformat(),rtt_ms=0,sha256="f"*64)
            m.update(book_receipt=r,metadata_receipt=r,limits={})
        return rows
    monkeypatch.setattr(ob,"utc",lambda:clock[0])
    monkeypatch.setattr(ob.Observer,"markets",markets)
    instance=runtime.make_observer(root)
    with instance.store.db:
        for key in PROFILES:
            a=dict(instance.store.accounts())[key]
            a.update(cash=200.,qty=[1.,0.,0.])
            instance.store.db.execute("UPDATE accounts SET payload=? WHERE id=?",(json.dumps(a),key))
    instance.last_export=1e30
    for minute in range(60):
        clock[0]=DAY+datetime.timedelta(hours=2,minutes=minute)
        instance.last_risk=-1e30
        instance.tick(DAY.isoformat())
        before=ledger(root)
        data=exported(instance,root,clock[0])
        assert all(p["equity_usdt"]==300+minute for p in data["profiles"])
        assert data["comparison"]["r2_minus_t01_usdt"]==0
        assert data["profiles"][0]["valuation_at"]==clock[0].isoformat()
        assert ledger(root)==before
    assert len(calls)==60
    assert instance.store.db.execute("SELECT count(*) FROM events WHERE kind='EQUITY'").fetchone()[0]==24
    assert instance.store.db.execute("SELECT count(*) FROM events WHERE kind='DATA_QUALITY'").fetchone()[0]==0
    instance.store.db.close()

@pytest.mark.parametrize("age,expected",[(0,300),(120,300),(121,None),(-1,None)])
def test_quote_age_not_export_time(tmp_path,age,expected):
    root=prepared(tmp_path)
    quotes=QuoteCache()
    quotes.observe(quote_set(DAY),DAY)
    data=export(root,root/"telemetry.json",now=DAY+datetime.timedelta(seconds=age),quotes=quotes)
    assert data["profiles"][0]["equity_usdt"]==expected
    if expected is not None:assert data["profiles"][0]["valuation_at"]==DAY.isoformat()
    else:assert data["comparison"]["r2_minus_t01_usdt"] is None

@pytest.mark.parametrize("bad",["missing","future","stale","wrong_symbol","crossed","wrong_mid","late_available"])
def test_invalid_quote_batch_never_accepted(bad):
    quotes=QuoteCache()
    rows=quote_set(DAY)
    if bad=="missing":rows.pop()
    elif bad=="future":rows[0]["book"]["ts"]+=1000
    elif bad=="stale":rows[0]["book"]["ts"]-=6000
    elif bad=="wrong_symbol":rows[0]["book"]["s"]="ETHUSDT"
    elif bad=="crossed":rows[0]["bids"]=[(102,1)]
    elif bad=="wrong_mid":rows[0]["mid"]=110
    elif bad=="late_available":rows[0]["book_receipt"]["available_at"]=(DAY+datetime.timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError):quotes.observe(rows,DAY)
    assert quotes.current(DAY) is None

def test_current_balances_same_snapshot_and_no_history_rewrite(tmp_path):
    root=prepared(tmp_path)
    quotes=QuoteCache()
    quotes.observe(quote_set(DAY),DAY)
    db=sqlite3.connect(root/"observer.sqlite")
    a=json.loads(db.execute("SELECT payload FROM accounts WHERE id=?",(PROFILES[0],)).fetchone()[0])
    a.update(cash=200.,qty=[1.,0.,0.])
    db.execute("UPDATE accounts SET payload=? WHERE id=?",(json.dumps(a),PROFILES[0]))
    db.commit()
    before=ledger(root)
    result=export(root,root/"telemetry.json",now=DAY,quotes=quotes)
    assert result["profiles"][0]["equity_usdt"]==300
    assert result["profiles"][0]["equity_history"]==[]
    assert result["profiles"][0]["liquidatable_cash_usdt"] is None
    assert result["profiles"][0]["dust_usdt"] is None
    assert ledger(root)==before
    assert result["comparison"]["r2_minus_t01_usdt"]==0
    db.close()

def test_empty_cache_cannot_reuse_journal_mark(tmp_path):
    root=prepared(tmp_path)
    ob=runtime.frozen_modules()
    st=ob.Store(root)
    with st.db:
        a=dict(st.accounts())[PROFILES[0]]
        st.event("RISK:test:EQUITY:"+PROFILES[0],"EQUITY",dict(record_type="TEST",**a,mtm=999))
    data=export(root,root/"telemetry.json",now=datetime.datetime.now(UTC),quotes=QuoteCache())
    assert data["profiles"][0]["equity_usdt"] is None
    st.db.close()

def good_resources():
    return dict(free_disk_bytes=20*1024**3,mem_available_bytes=1024**3,free_inodes=10000,fd_soft_limit=4096,
                load_per_cpu=.1,runtime_files=10,runtime_bytes=100000)

def evidence_fixture(root,monkeypatch):
    from raw_store import Archive
    now=datetime.datetime.now(UTC)
    day=now.replace(hour=0,minute=0,second=0,microsecond=0)
    folder=root/"readiness"
    folder.mkdir()
    archive=Archive(folder/"raw",reserve=0)
    refs={}
    for symbol in ["BTCUSDT","ETHUSDT","SOLUSDT"]:
        start=int((day-datetime.timedelta(days=29)).timestamp()*1000)
        end=int(day.timestamp()*1000)
        rows=[]
        for i in range(29):
            price=100+i*.5
            rows.append([str(start+i*86400000),str(price),str(price+1),str(price-1),str(price)])
        meta=dict(symbol=symbol,status="Trading",lotSizeFilter=dict(basePrecision=".000001",quotePrecision=".000001",minOrderAmt="5",maxMarketOrderQty="100000"))
        book=dict(s=symbol,b=[["99","10000"]],a=[["101","10000"]],ts=int(datetime.datetime.now(UTC).timestamp()*1000))
        objects={"kline":dict(retCode=0,result=dict(list=rows)),"meta":dict(retCode=0,result=dict(category="spot",list=[meta])),"book":dict(retCode=0,result=book)}
        for kind,obj in objects.items():
            path={"kline":"kline","meta":"instruments-info","book":"orderbook"}[kind]
            url="https://api.bybit.com/v5/market/"+path+"?category=spot&symbol="+symbol
            if kind=="kline":url+="&interval=D&start="+str(start)+"&end="+str(end-1)
            body=json.dumps(obj).encode()
            rid=hashlib.sha256((kind+symbol).encode()).hexdigest()[:32]
            receipt=archive.capture(io.BytesIO(body),dict(id=rid,url=url,http_status=200,available_at=now.isoformat()))
            refs[kind+":"+symbol]=receipt["file"]
    when=datetime.datetime.now(UTC)
    env=dict(system="Linux",host_fingerprint="SYNTHETIC_TEST_HOST_NOT_VPS",python=[3,12],numpy="2.5.4",sqlite=sqlite3.sqlite_version)
    monkeypatch.setattr(readiness,"environment",lambda:env)
    monkeypatch.setattr(readiness,"resource_sample",lambda root:good_resources())
    result=readiness.functional_result(refs,folder/"raw",day,when)
    expected=dict(system="Linux",lock_exclusive=True,crash_exit=23,cash_after_crash=300,integrity="ok",python=[3,12],numpy="2.5.4",sqlite=sqlite3.sqlite_version)
    reports={"linux.json":dict(probe_sha256=hashlib.sha256(readiness.LINUX_PROBE.encode()).hexdigest(),returncode=0,result=expected),
        "linux_trace.json":dict(returncode=0,stdout=json.dumps(expected),stderr=""),
        "functional.json":dict(day=day.isoformat(),raw_references=refs,result=result),"resources.json":good_resources()}
    for name,obj in reports.items():(folder/name).write_text(json.dumps(obj))
    manifest=dict(format=readiness.FORMAT,environment=copy.deepcopy(env),runtime_path=str(root.resolve()),collected_at=when.isoformat(),
        cohort_sha256=readiness.canonical(cohort()),frozen_manifest_sha256=readiness.sha(runtime.HERE/"FROZEN_MANIFEST.json"),
        code_sha256=readiness.code_hashes(),files={n:readiness.sha(folder/n) for n in readiness.FILES})
    (folder/"manifest.json").write_text(json.dumps(manifest))
    policy=dict(deployment_authorized=True,readiness_manifest_sha256=readiness.sha(folder/"manifest.json"),
        user_authorization=dict(scope="R2_SHADOW_START",reference="SYNTHETIC_TEST_ONLY_NOT_ACTUAL_APPROVAL",t0_utc=DAY.isoformat()))
    return folder,manifest,reports,policy,when

def rewrite(folder,manifest,policy):
    manifest["files"]={n:readiness.sha(folder/n) for n in readiness.FILES}
    (folder/"manifest.json").write_text(json.dumps(manifest))
    policy["readiness_manifest_sha256"]=readiness.sha(folder/"manifest.json")

def test_complete_readiness_validation_offline_fixture_only(tmp_path,monkeypatch):
    root=prepared(tmp_path)
    folder,m,reports,policy,when=evidence_fixture(root,monkeypatch)
    result=readiness.validate(root,policy,cohort(),now=when)
    assert result["functional_profiles"]==24 and result["status"]=="PASS"
    assert result["host_fingerprint"]=="SYNTHETIC_TEST_HOST_NOT_VPS"

@pytest.mark.parametrize("fault",["expired","future","host","path","cohort","code","frozen","missing_file","hash",
    "linux_exit","linux_probe","linux_trace","functional_balance","raw_missing","bad_resource","live_resource","no_user","no_permission","no_pin","wrong_platform","missing_artifact_set"])
def test_readiness_rejects_invalid_actual_results(tmp_path,monkeypatch,fault):
    root=prepared(tmp_path)
    folder,m,reports,policy,when=evidence_fixture(root,monkeypatch)
    if fault=="expired":m["collected_at"]=(when-datetime.timedelta(hours=2)).isoformat()
    elif fault=="future":m["collected_at"]=(when+datetime.timedelta(seconds=1)).isoformat()
    elif fault=="host":m["environment"]["host_fingerprint"]="other"
    elif fault=="path":m["runtime_path"]="/other"
    elif fault=="cohort":m["cohort_sha256"]="bad"
    elif fault=="code":m["code_sha256"]["marks.py"]="bad"
    elif fault=="frozen":m["frozen_manifest_sha256"]="bad"
    elif fault=="linux_exit":reports["linux.json"]["returncode"]=1
    elif fault=="linux_probe":reports["linux.json"]["probe_sha256"]="bad"
    elif fault=="linux_trace":reports["linux_trace.json"]["stdout"]="{}"
    elif fault=="functional_balance":next(iter(reports["functional.json"]["result"]["accounts"].values()))["cash"]+=1
    elif fault=="bad_resource":reports["resources.json"]["mem_available_bytes"]=1
    elif fault=="live_resource":monkeypatch.setattr(readiness,"resource_sample",lambda root:dict(good_resources(),free_disk_bytes=1))
    elif fault=="no_user":policy["user_authorization"]={}
    elif fault=="no_permission":policy["deployment_authorized"]=False
    elif fault=="wrong_platform":monkeypatch.setattr(readiness,"environment",lambda:dict(m["environment"],system="Windows"))
    for n,r in reports.items():(folder/n).write_text(json.dumps(r))
    rewrite(folder,m,policy)
    if fault=="missing_file":(folder/"linux_trace.json").unlink()
    elif fault=="hash":(folder/"linux.json").write_text("{}")
    elif fault=="raw_missing":next((folder/"raw").glob("*.sqlite")).unlink()
    elif fault=="no_pin":policy["readiness_manifest_sha256"]=None
    elif fault=="missing_artifact_set":
        m["files"].pop("linux.json")
        (folder/"manifest.json").write_text(json.dumps(m))
        policy["readiness_manifest_sha256"]=readiness.sha(folder/"manifest.json")
    with pytest.raises(RuntimeError,match="READINESS"):
        readiness.validate(root,policy,cohort(),now=when)

@pytest.mark.parametrize("key,value",[("free_disk_bytes",1),("mem_available_bytes",1),("free_inodes",1),
    ("fd_soft_limit",1),("load_per_cpu",.81),("runtime_files",3001),("runtime_bytes",11*1024**3),("mem_available_bytes",True)])
def test_resource_measurements_enforced(key,value):
    with pytest.raises(RuntimeError,match="READINESS_RESOURCE"):
        readiness.check_resources(dict(good_resources(),**{key:value}))

def test_collection_not_authorized_has_no_side_effects(tmp_path):
    from collect_readiness import collect
    root=tmp_path/"nonexistent"
    with pytest.raises(RuntimeError,match="NOT_AUTHORIZED"):collect(root,{})
    assert not root.exists()

def test_readiness_checks_before_any_lock_or_network_with_all_flags(tmp_path,monkeypatch):
    root=prepared(tmp_path)
    policy=dict(deployment_authorized=True,vps_gate_passed=True,three_participants_ready=True,
        telemetry_contract_accepted=True,local_verification_passed=True,authorized_start_utc=DAY.isoformat())
    path=tmp_path/"policy.json"
    path.write_text(json.dumps(policy))
    ob=runtime.frozen_modules()
    monkeypatch.setattr(ob,"process_lock",lambda *a:pytest.fail("No lock before readiness"))
    before=ledger(root)
    with pytest.raises(RuntimeError,match="READINESS"):runtime.run(root,path)
    assert ledger(root)==before and not (root/"START_RECEIPT.json").exists()

def test_recovery_evidence_has_nonzero_all24_and_hard_crashes(tmp_path):
    from recovery_scenario import experiment
    result=experiment(tmp_path/"recovery")
    assert result["reference_order_events"]>0
    assert result["reference_nonzero_profiles"]==24
    assert {s["exit_code"] for s in result["scenarios"]}=={23,24}
    for s in result["scenarios"]:
        assert len(s["per_profile"])==24
        assert all(p["positive_legs"]>0 and p["matches_reference"] for p in s["per_profile"].values())
