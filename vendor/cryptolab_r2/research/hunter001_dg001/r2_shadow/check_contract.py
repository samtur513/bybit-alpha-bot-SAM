"""Offline checks for the JSON Schema keyword subset used by our fixed contract."""
from pathlib import Path
import copy, json, math
HERE=Path(__file__).resolve().parent

def validate(value,s):
    if "oneOf" in s:
        matches=0
        for candidate in s["oneOf"]:
            try:
                validate(value,candidate)
                matches+=1
            except ValueError:
                pass
        if matches!=1:raise ValueError("oneOf")
        return
    t=s.get("type")
    kinds=[t] if isinstance(t,str) else t
    types={"null":lambda v:v is None,"boolean":lambda v:type(v) is bool,
        "number":lambda v:type(v) in (int,float) and math.isfinite(v),
        "integer":lambda v:type(v) is int,"string":lambda v:isinstance(v,str),
        "object":lambda v:isinstance(v,dict),"array":lambda v:isinstance(v,list)}
    if kinds and not any(types[k](value) for k in kinds):raise ValueError("type")
    def equal(a,b):
        return a==b and (type(a) is bool)==(type(b) is bool)
    if "const" in s and not equal(value,s["const"]):raise ValueError("const")
    if "enum" in s and not any(equal(value,x) for x in s["enum"]):raise ValueError("enum")
    if type(value) in (int,float):
        if value<s.get("minimum",-math.inf) or value>s.get("maximum",math.inf):raise ValueError("range")
    if isinstance(value,dict):
        if not set(s.get("required",[]))<=set(value):raise ValueError("required")
        properties=s.get("properties",{})
        if s.get("additionalProperties") is False and not set(value)<=set(properties):raise ValueError("additionalProperties")
        for key,item in value.items():
            if key in properties:validate(item,properties[key])
    if isinstance(value,list):
        if len(value)<s.get("minItems",0) or len(value)>s.get("maxItems",math.inf):raise ValueError("items")
        for item in value:validate(item,s.get("items",{}))

def check():
    schema=json.loads((HERE/"telemetry.schema.json").read_text())
    data=json.loads((HERE/"evidence/telemetry.example.json").read_text())
    validate(data,schema)
    # Both absent and numeric current valuations must be valid.
    fresh=copy.deepcopy(data)
    for p in fresh["profiles"]:
        p.update(equity_usdt=300.,net_pnl_usdt=0.,net_return_pct=0.,valuation_status="FRESH")
    validate(fresh,schema)
    rejected=0
    for change in ["extra_private_field","version","negative_cash","missing_profile","boolean_orders","bad_weights"]:
        bad=copy.deepcopy(data)
        if change=="extra_private_field":bad["private_code"]="forbidden"
        elif change=="version":bad["schema_version"]="unknown"
        elif change=="negative_cash":bad["profiles"][0]["cash_usdt"]=-1
        elif change=="missing_profile":bad["profiles"].pop()
        elif change=="boolean_orders":bad["real_orders"]=False
        elif change=="bad_weights":bad["target_weights"]=[-1,0,0]
        try:
            validate(bad,schema)
        except ValueError:
            rejected+=1
        else:
            raise RuntimeError("INVALID_CONTRACT_ACCEPTED: "+change)
    result=dict(status="PASS",valid_examples=2,invalid_examples_rejected=rejected,
        validator="LOCAL_FIXED_KEYWORD_SUBSET; not a complete Draft202012 implementation",
        receiver_integration="PENDING")
    (HERE/"evidence/CONTRACT_CHECK.json").write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    return result

if __name__=="__main__":print(json.dumps(check()))
