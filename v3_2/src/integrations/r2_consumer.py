"""
v3_2/src/integrations/r2_consumer.py
Read-only Telemetry Consumer for CryptoLab R2-SHADOW.
Contract: docs/integrations/r2_shadow/TELEMETRY_CONTRACT.md
Schema: docs/integrations/r2_shadow/telemetry.schema.json
Version: r2-shadow-telemetry/1
"""

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field


SCHEMA_VERSION = "r2-shadow-telemetry/1"
SYSTEM_NAME = "CRYPTOLAB_R2_SHADOW"
MARKET_TYPE = "BYBIT_SPOT_LONG_CASH_NO_LEVERAGE"

EXPECTED_PROFILES = [
    "R2:300:BASE:OBSERVED_BOOK",
    "R2:300:STRESS:OBSERVED_BOOK",
    "BASELINE:300:BASE:OBSERVED_BOOK",
]

MAX_SNAPSHOT_AGE_SEC = 45.0
MAX_HEARTBEAT_AGE_SEC = 45.0
MAX_VALUATION_AGE_SEC = 120.0
MAX_FUTURE_CLOCK_DRIFT_SEC = 15.0


class R2ValidationError(Exception):
    """Raised when telemetry payload violates the JSON schema or wire contract."""
    pass


def _validate_schema_node(value: Any, s: Dict[str, Any], path: str = "root") -> None:
    """
    Zero-dependency JSON Schema validator supporting the exact subset
    used by telemetry.schema.json.
    """
    if "oneOf" in s:
        matches = 0
        last_err = None
        for candidate in s["oneOf"]:
            try:
                _validate_schema_node(value, candidate, path)
                matches += 1
            except R2ValidationError as e:
                last_err = e
        if matches != 1:
            raise R2ValidationError(f"Field '{path}' did not match exactly one candidate in oneOf (matched {matches}): {last_err}")
        return

    t = s.get("type")
    kinds = [t] if isinstance(t, str) else (t or [])
    types = {
        "null": lambda v: v is None,
        "boolean": lambda v: type(v) is bool,
        "number": lambda v: (type(v) in (int, float)) and math.isfinite(v),
        "integer": lambda v: type(v) is int and not isinstance(v, bool),
        "string": lambda v: isinstance(v, str),
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
    }
    if kinds and not any(types[k](value) for k in kinds):
        raise R2ValidationError(f"Field '{path}' expected type in {kinds}, got {type(value).__name__} ({value})")

    def equal(a: Any, b: Any) -> bool:
        return a == b and (type(a) is bool) == (type(b) is bool)

    if "const" in s and not equal(value, s["const"]):
        raise R2ValidationError(f"Field '{path}' expected const '{s['const']}', got '{value}'")

    if "enum" in s and not any(equal(value, x) for x in s["enum"]):
        raise R2ValidationError(f"Field '{path}' value '{value}' not in allowed enum {s['enum']}")

    if type(value) in (int, float) and not isinstance(value, bool):
        min_v = s.get("minimum", -math.inf)
        max_v = s.get("maximum", math.inf)
        if value < min_v or value > max_v:
            raise R2ValidationError(f"Field '{path}' value {value} out of range [{min_v}, {max_v}]")

    if isinstance(value, dict):
        required = set(s.get("required", []))
        actual = set(value.keys())
        missing = required - actual
        if missing:
            raise R2ValidationError(f"Field '{path}' missing required keys: {sorted(missing)}")

        properties = s.get("properties", {})
        if s.get("additionalProperties") is False:
            extra = actual - set(properties.keys())
            if extra:
                raise R2ValidationError(f"Field '{path}' contains forbidden additional properties: {sorted(extra)}")

        for key, item in value.items():
            if key in properties:
                _validate_schema_node(item, properties[key], f"{path}.{key}")

    if isinstance(value, list):
        min_items = s.get("minItems", 0)
        max_items = s.get("maxItems", math.inf)
        if len(value) < min_items or len(value) > max_items:
            raise R2ValidationError(f"Field '{path}' item count {len(value)} outside [{min_items}, {max_items}]")
        item_schema = s.get("items", {})
        for idx, item in enumerate(value):
            _validate_schema_node(item, item_schema, f"{path}[{idx}]")


def parse_iso_utc(ts_str: Optional[str]) -> Optional[datetime]:
    """Parses ISO 8601 UTC timestamp string into timezone-aware datetime."""
    if not ts_str:
        return None
    try:
        # Normalize Z suffix to +00:00
        clean = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


@dataclass
class R2ConsumerResult:
    """Sanitized, read-only validated snapshot from CryptoLab R2-SHADOW."""
    is_valid: bool
    status: str  # "ARMED", "OBSERVING", "STALE", "INVALID", "NO_FRESH_HEARTBEAT"
    rejection_reason: Optional[str] = None
    raw_payload: Optional[Dict[str, Any]] = None
    generated_at: Optional[datetime] = None
    heartbeat_at: Optional[datetime] = None
    snapshot_age_sec: Optional[float] = None
    
    # Valuation and performance metrics for primary profile (R2:300:BASE)
    r2_base_profile: Optional[str] = None
    valuation_status: str = "UNVALUED_OR_STALE"
    valuation_at: Optional[datetime] = None
    valuation_age_sec: Optional[float] = None
    equity_usdt: Optional[float] = None
    net_pnl_usdt: Optional[float] = None
    net_return_pct: Optional[float] = None
    mdd_pct: float = 0.0
    cash_usdt: float = 300.0
    quantities: Dict[str, float] = field(default_factory=dict)
    exposure_pct: Optional[float] = None
    cash_fraction_pct: Optional[float] = None
    
    # Comparative edge over T01 baseline
    r2_minus_t01_usdt: Optional[float] = None
    r2_minus_t01_pp: Optional[float] = None
    comparison_status: str = "UNPAIRED_OR_UNVALUED"
    
    # Costs and execution counters
    fees_usdt: float = 0.0
    buffer_usdt: float = 0.0
    spread_impact_usdt: float = 0.0
    turnover_usdt: float = 0.0
    missed_decisions: int = 0
    missed_executions: int = 0
    data_errors: int = 0
    error_count: int = 0
    
    # Cohort schedule
    cohort_t0_utc: Optional[str] = None
    cohort_minimum_end_utc: Optional[str] = None
    cohort_schedule_active: bool = False
    
    # Safety capability assertions
    real_orders: int = 0
    demo_orders: int = 0
    deployment_status: str = "NOT_DEPLOYED"


class R2TelemetryConsumer:
    """
    Read-only adapter that polls or ingests telemetry.json from CryptoLab R2-SHADOW.
    Never mutates internal state, never creates orders, strictly adheres to TELEMETRY_CONTRACT.md.
    """

    def __init__(
        self,
        schema_path: Optional[Path] = None,
        max_snapshot_age_sec: float = MAX_SNAPSHOT_AGE_SEC,
        max_heartbeat_age_sec: float = MAX_HEARTBEAT_AGE_SEC,
        max_valuation_age_sec: float = MAX_VALUATION_AGE_SEC,
    ):
        if schema_path is None:
            # Resolve repo root (4 levels up from this file) or current working directory
            candidates = [
                Path(__file__).resolve().parents[3] / "docs" / "integrations" / "r2_shadow" / "telemetry.schema.json",
                Path.cwd() / "docs" / "integrations" / "r2_shadow" / "telemetry.schema.json",
                Path(__file__).resolve().parent.parent.parent / "docs" / "integrations" / "r2_shadow" / "telemetry.schema.json",
            ]
            schema_path = next((c for c in candidates if c.exists()), candidates[0])
        
        self.schema_path = schema_path
        self.max_snapshot_age_sec = max_snapshot_age_sec
        self.max_heartbeat_age_sec = max_heartbeat_age_sec
        self.max_valuation_age_sec = max_valuation_age_sec
        self._schema_cache: Optional[Dict[str, Any]] = None

    def _load_schema(self) -> Dict[str, Any]:
        if self._schema_cache is None:
            if not self.schema_path.exists():
                raise FileNotFoundError(f"R2 Telemetry schema not found at: {self.schema_path}")
            self._schema_cache = json.loads(self.schema_path.read_text(encoding="utf-8"))
        return self._schema_cache

    def validate_schema(self, payload: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Validates payload against telemetry.schema.json."""
        try:
            schema = self._load_schema()
            _validate_schema_node(payload, schema)
            return True, None
        except Exception as e:
            return False, str(e)

    def parse_and_validate(
        self,
        payload_or_json: Any,
        now_dt: Optional[datetime] = None,
    ) -> R2ConsumerResult:
        """
        Parses and validates a CryptoLab R2 telemetry payload.
        Ensures strict contract compliance, temporal freshness, and profile ordering.
        """
        if now_dt is None:
            now_dt = datetime.now(timezone.utc)
        elif now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)

        # 1. Parse JSON if string or bytes
        if isinstance(payload_or_json, (str, bytes)):
            try:
                payload = json.loads(payload_or_json)
            except Exception as e:
                return R2ConsumerResult(
                    is_valid=False,
                    status="INVALID",
                    rejection_reason=f"Corrupted or invalid JSON string: {e}",
                )
        elif isinstance(payload_or_json, dict):
            payload = payload_or_json
        else:
            return R2ConsumerResult(
                is_valid=False,
                status="INVALID",
                rejection_reason=f"Expected dict or JSON string, got {type(payload_or_json).__name__}",
            )

        # 2. Strict Schema Validation (enforcing no additionalProperties)
        valid, err = self.validate_schema(payload)
        if not valid:
            return R2ConsumerResult(
                is_valid=False,
                status="INVALID",
                rejection_reason=f"Schema violation: {err}",
                raw_payload=payload,
            )

        # 3. High-level Contract Invariant Checks
        if payload.get("schema_version") != SCHEMA_VERSION:
            return R2ConsumerResult(
                is_valid=False,
                status="INVALID",
                rejection_reason=f"Unknown schema_version '{payload.get('schema_version')}', expected '{SCHEMA_VERSION}'",
                raw_payload=payload,
            )

        if payload.get("system") != SYSTEM_NAME:
            return R2ConsumerResult(
                is_valid=False,
                status="INVALID",
                rejection_reason=f"Unexpected system '{payload.get('system')}', expected '{SYSTEM_NAME}'",
                raw_payload=payload,
            )

        if payload.get("market") != MARKET_TYPE:
            return R2ConsumerResult(
                is_valid=False,
                status="INVALID",
                rejection_reason=f"Unsupported market '{payload.get('market')}', expected '{MARKET_TYPE}'",
                raw_payload=payload,
            )

        # 4. Profile Integrity and Ordering
        profiles = payload.get("profiles", [])
        if len(profiles) != 3:
            return R2ConsumerResult(
                is_valid=False,
                status="INVALID",
                rejection_reason=f"Expected exactly 3 profiles, found {len(profiles)}",
                raw_payload=payload,
            )

        for expected_name, prof in zip(EXPECTED_PROFILES, profiles):
            if prof.get("profile") != expected_name:
                return R2ConsumerResult(
                    is_valid=False,
                    status="INVALID",
                    rejection_reason=f"Profiles order mismatch: expected '{expected_name}', got '{prof.get('profile')}'",
                    raw_payload=payload,
                )

        # 5. Temporal Freshness Verification
        gen_dt = parse_iso_utc(payload.get("generated_at"))
        if not gen_dt:
            return R2ConsumerResult(
                is_valid=False,
                status="INVALID",
                rejection_reason="Missing or unparseable generated_at timestamp",
                raw_payload=payload,
            )

        snapshot_age = (now_dt - gen_dt).total_seconds()
        if snapshot_age < -MAX_FUTURE_CLOCK_DRIFT_SEC:
            return R2ConsumerResult(
                is_valid=False,
                status="INVALID",
                rejection_reason=f"Future timestamp clock drift ({snapshot_age:.1f}s in future)",
                raw_payload=payload,
                generated_at=gen_dt,
                snapshot_age_sec=snapshot_age,
            )

        if snapshot_age > self.max_snapshot_age_sec:
            # Stale snapshot
            is_stale_snapshot = True
        else:
            is_stale_snapshot = False

        # 6. Process & Heartbeat Check
        hb_dt = parse_iso_utc(payload.get("heartbeat_at"))
        hb_age = (now_dt - hb_dt).total_seconds() if hb_dt else None
        
        proc_status = payload.get("process_status", "NO_FRESH_HEARTBEAT")
        if is_stale_snapshot:
            final_status = "STALE"
        elif hb_age is None or hb_age > self.max_heartbeat_age_sec:
            final_status = "NO_FRESH_HEARTBEAT"
        else:
            final_status = proc_status

        # 7. Extract Primary Profile Metrics (R2:300:BASE:OBSERVED_BOOK)
        r2_base = profiles[0]
        val_status = r2_base.get("valuation_status", "UNVALUED_OR_STALE")
        val_dt = parse_iso_utc(r2_base.get("valuation_at"))
        val_age = (now_dt - val_dt).total_seconds() if val_dt else None

        # Rule: If valuation_at is stale (>120s) or missing, equity/pnl/return MUST BE NULL
        is_valuation_fresh = (
            val_status == "FRESH"
            and val_age is not None
            and 0.0 <= val_age <= self.max_valuation_age_sec
            and not is_stale_snapshot
        )

        if is_valuation_fresh:
            effective_val_status = "FRESH"
            equity = float(r2_base["equity_usdt"]) if r2_base.get("equity_usdt") is not None else None
            net_pnl = float(r2_base["net_pnl_usdt"]) if r2_base.get("net_pnl_usdt") is not None else None
            net_return = float(r2_base["net_return_pct"]) if r2_base.get("net_return_pct") is not None else None
            exposure = float(r2_base["exposure_pct"]) if r2_base.get("exposure_pct") is not None else None
            cash_fraction = float(r2_base["cash_fraction_pct"]) if r2_base.get("cash_fraction_pct") is not None else None
        else:
            effective_val_status = "UNVALUED_OR_STALE"
            equity = None
            net_pnl = None
            net_return = None
            exposure = None
            cash_fraction = None

        # Profit Factor Check: LOT_REALIZATION_NOT_DEFINED
        if r2_base.get("profit_factor") is not None:
            return R2ConsumerResult(
                is_valid=False,
                status="INVALID",
                rejection_reason="profit_factor must be null per contract specification",
                raw_payload=payload,
            )

        # 8. Comparison (R2 vs T01)
        comp = payload.get("comparison", {})
        baseline = profiles[2]
        base_val_dt = parse_iso_utc(baseline.get("valuation_at"))
        base_val_age = (now_dt - base_val_dt).total_seconds() if base_val_dt else None
        base_val_fresh = (
            baseline.get("valuation_status") == "FRESH"
            and base_val_age is not None
            and 0.0 <= base_val_age <= self.max_valuation_age_sec
            and not is_stale_snapshot
        )

        if is_valuation_fresh and base_val_fresh and comp.get("status") == "PAIRED_PRELIMINARY":
            r2_minus_t01_usdt = float(comp["r2_minus_t01_usdt"]) if comp.get("r2_minus_t01_usdt") is not None else None
            r2_minus_t01_pp = float(comp["r2_minus_t01_pp"]) if comp.get("r2_minus_t01_pp") is not None else None
            comparison_status = "PAIRED_PRELIMINARY"
        else:
            r2_minus_t01_usdt = None
            r2_minus_t01_pp = None
            comparison_status = "UNPAIRED_OR_UNVALUED"

        # 9. Costs and totals
        totals = r2_base.get("totals", {})
        fees_usdt = float(totals.get("fees_usdt", 0.0))
        buffer_usdt = float(totals.get("buffer_usdt", 0.0))
        spread_impact_usdt = float(totals.get("spread_impact_usdt", 0.0))
        turnover_usdt = float(totals.get("turnover_usdt", 0.0))

        # 10. Operational Counters
        missed_dec = int(payload.get("missed_decisions", 0))
        missed_exec = int(payload.get("missed_executions", 0))
        data_errs = int(payload.get("data_errors", 0))
        total_errs = missed_dec + missed_exec + data_errs

        # Cohort info
        cohort = payload.get("cohort", {})

        return R2ConsumerResult(
            is_valid=True,
            status=final_status,
            rejection_reason=None if not is_stale_snapshot else f"Snapshot age ({snapshot_age:.1f}s) exceeded limit ({self.max_snapshot_age_sec}s)",
            raw_payload=payload,
            generated_at=gen_dt,
            heartbeat_at=hb_dt,
            snapshot_age_sec=snapshot_age,
            r2_base_profile=r2_base.get("profile"),
            valuation_status=effective_val_status,
            valuation_at=val_dt,
            valuation_age_sec=val_age,
            equity_usdt=equity,
            net_pnl_usdt=net_pnl,
            net_return_pct=net_return,
            mdd_pct=float(r2_base.get("mdd_pct", 0.0)),
            cash_usdt=float(r2_base.get("cash_usdt", 300.0)),
            quantities={k: float(v) for k, v in r2_base.get("quantities", {}).items()},
            exposure_pct=exposure,
            cash_fraction_pct=cash_fraction,
            r2_minus_t01_usdt=r2_minus_t01_usdt,
            r2_minus_t01_pp=r2_minus_t01_pp,
            comparison_status=comparison_status,
            fees_usdt=fees_usdt,
            buffer_usdt=buffer_usdt,
            spread_impact_usdt=spread_impact_usdt,
            turnover_usdt=turnover_usdt,
            missed_decisions=missed_dec,
            missed_executions=missed_exec,
            data_errors=data_errs,
            error_count=total_errs,
            cohort_t0_utc=cohort.get("t0_utc"),
            cohort_minimum_end_utc=cohort.get("minimum_end_utc"),
            cohort_schedule_active=bool(cohort.get("schedule_active", False)),
            real_orders=int(payload.get("real_orders", 0)),
            demo_orders=int(payload.get("demo_orders", 0)),
            deployment_status=payload.get("deployment_status", "NOT_DEPLOYED"),
        )

    def read_telemetry_file(
        self,
        file_path: Any,
        now_dt: Optional[datetime] = None,
    ) -> R2ConsumerResult:
        """Reads and validates an atomic UTF-8 telemetry.json file."""
        p = Path(file_path)
        if not p.exists():
            return R2ConsumerResult(
                is_valid=False,
                status="INVALID",
                rejection_reason=f"Telemetry file does not exist: {p}",
            )
        try:
            content = p.read_text(encoding="utf-8")
            return self.parse_and_validate(content, now_dt=now_dt)
        except Exception as e:
            return R2ConsumerResult(
                is_valid=False,
                status="INVALID",
                rejection_reason=f"Failed to read file: {e}",
            )

    def get_dashboard_summary(self, result: R2ConsumerResult) -> Dict[str, Any]:
        """Provides a sanitized summary suitable for the unified 3-system dashboard."""
        return {
            "system": "CryptoLab R2-SHADOW",
            "market": "Bybit Spot Long-Only (No Leverage)",
            "primary_profile": result.r2_base_profile or "R2:300:BASE:OBSERVED_BOOK",
            "process_status": result.status,
            "deployment_status": result.deployment_status,
            "valuation_status": result.valuation_status,
            "is_valid": result.is_valid,
            "rejection_reason": result.rejection_reason,
            "equity_usdt": result.equity_usdt,
            "net_pnl_usdt": result.net_pnl_usdt,
            "net_return_pct": result.net_return_pct,
            "mdd_pct": result.mdd_pct,
            "cash_usdt": result.cash_usdt,
            "exposure_pct": result.exposure_pct,
            "quantities": result.quantities,
            "r2_minus_t01_usdt": result.r2_minus_t01_usdt,
            "r2_minus_t01_pp": result.r2_minus_t01_pp,
            "comparison_status": result.comparison_status,
            "costs": {
                "fees_usdt": result.fees_usdt,
                "buffer_usdt": result.buffer_usdt,
                "spread_impact_usdt": result.spread_impact_usdt,
                "turnover_usdt": result.turnover_usdt,
            },
            "counters": {
                "missed_decisions": result.missed_decisions,
                "missed_executions": result.missed_executions,
                "data_errors": result.data_errors,
            },
            "timestamps": {
                "generated_at": result.generated_at.isoformat() if result.generated_at else None,
                "heartbeat_at": result.heartbeat_at.isoformat() if result.heartbeat_at else None,
                "valuation_at": result.valuation_at.isoformat() if result.valuation_at else None,
                "snapshot_age_sec": round(result.snapshot_age_sec, 1) if result.snapshot_age_sec is not None else None,
            },
            "safety": {
                "real_orders": result.real_orders,
                "demo_orders": result.demo_orders,
            },
        }
