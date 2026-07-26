import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any


PUBLICATION_LEDGER_SCHEMA_VERSION = "recommendation-publication-ledger/v1"


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _finite_positive(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) > 0
    )


def _string_list(value: Any) -> bool:
    return (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes))
        and bool(value)
        and all(_nonempty_string(item) for item in value)
    )


def _nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _sha256_hex(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _valid_iso_date(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 10:
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _valid_iso_datetime(value: Any) -> bool:
    if not _nonempty_string(value):
        return False
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def canonical_recommendation_symbol(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    return text.split(".", 1)[0]


def recommendation_operation_contract_errors(
    item: Mapping[str, Any],
) -> tuple[str, ...]:
    errors: list[str] = []
    if not canonical_recommendation_symbol(item.get("symbol")):
        errors.append("symbol")
    zone = item.get("entry_zone")
    levels = item.get("levels")
    plans = item.get("trade_plans")
    advice = item.get("operation_advice")
    if not isinstance(zone, Mapping) or any(
        not _finite_positive(zone.get(key)) for key in ("low", "high")
    ):
        errors.append("entry_zone")
    if not isinstance(levels, Mapping) or any(
        not _finite_positive(levels.get(key))
        for key in ("support", "resistance", "stop_loss", "take_profit")
    ):
        errors.append("levels")
    short_plan = plans.get("short_term") if isinstance(plans, Mapping) else None
    if not isinstance(short_plan, Mapping) or not _nonempty_string(
        short_plan.get("horizon") or short_plan.get("holding_period")
    ):
        errors.append("holding_plan")
    if item.get("market") != "a":
        errors.append("market")
    if item.get("auto_order") is not False:
        errors.append("auto_order")
    if not isinstance(advice, Mapping):
        errors.append("operation_advice")
        advice = {}
    advice_zone = advice.get("entry_zone")
    if not _nonempty_string(advice.get("action")):
        errors.append("operation_action")
    if not isinstance(advice_zone, Mapping) or any(
        not _finite_positive(advice_zone.get(key)) for key in ("low", "high")
    ):
        errors.append("operation_entry_zone")
    for key in ("stop_loss", "take_profit"):
        if not _finite_positive(advice.get(key)):
            errors.append(key)
    for key in ("holding_period", "invalidation", "expected_holding_period"):
        if not _nonempty_string(advice.get(key)):
            errors.append(key)
    for key in ("trigger_conditions", "invalidation_conditions"):
        if not _string_list(advice.get(key)):
            errors.append(key)
    take_profit_or_reduce = advice.get("take_profit_or_reduce")
    if (
        not isinstance(take_profit_or_reduce, Mapping)
        or not _nonempty_string(take_profit_or_reduce.get("condition"))
        or not _nonempty_string(take_profit_or_reduce.get("action"))
        or not _finite_positive(take_profit_or_reduce.get("trigger_price"))
        or take_profit_or_reduce.get("trigger_price")
        != advice.get("take_profit")
    ):
        errors.append("take_profit_or_reduce")
    if not _string_list(item.get("risks")):
        errors.append("risks")
    if (
        isinstance(zone, Mapping)
        and isinstance(levels, Mapping)
        and all(
            _finite_positive(value)
            for value in (
                zone.get("low"),
                zone.get("high"),
                levels.get("stop_loss"),
                levels.get("take_profit"),
            )
        )
        and not (
            float(levels["stop_loss"])
            < float(zone["low"])
            <= float(zone["high"])
            < float(levels["take_profit"])
        )
    ):
        errors.append("price_order")
    if (
        isinstance(zone, Mapping)
        and isinstance(advice_zone, Mapping)
        and any(
            advice_zone.get(key) != zone.get(key) for key in ("low", "high")
        )
    ):
        errors.append("entry_zone_mismatch")
    if isinstance(levels, Mapping):
        if advice.get("stop_loss") != levels.get("stop_loss"):
            errors.append("stop_loss_mismatch")
        if advice.get("take_profit") != levels.get("take_profit"):
            errors.append("take_profit_mismatch")
    if (
        advice.get("holding_period")
        != advice.get("expected_holding_period")
    ):
        errors.append("holding_period_mismatch")
    return tuple(errors)


def cap_daily_publications(
    items: Sequence[Mapping[str, Any]],
    prior_symbols: set[str],
    *,
    max_recommendations: int = 3,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    seen = {
        canonical_recommendation_symbol(symbol)
        for symbol in prior_symbols
        if canonical_recommendation_symbol(symbol)
    }
    remaining = max(0, int(max_recommendations) - len(seen))
    published: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for value in items:
        item = dict(value)
        symbol = canonical_recommendation_symbol(item.get("symbol"))
        if not symbol:
            rejected.append(item)
            continue
        if symbol in seen:
            published.append(item)
            continue
        if remaining <= 0:
            rejected.append(item)
            continue
        seen.add(symbol)
        remaining -= 1
        published.append(item)
    return published, rejected


def recommendation_snapshot_publication_errors(
    payload: Mapping[str, Any],
    *,
    require_receipt: bool = True,
) -> tuple[str, ...]:
    errors: list[str] = []
    items = payload.get("items")
    if not isinstance(items, list):
        return ("items",)
    if len(items) > 3:
        errors.append("item_limit")
    if (
        "auto_order" in payload
        and payload.get("auto_order") is not False
    ):
        errors.append("auto_order")
    item_symbols: set[str] = set()
    for item in items:
        if not isinstance(item, Mapping):
            errors.append("item_type")
            continue
        symbol = canonical_recommendation_symbol(item.get("symbol"))
        if symbol in item_symbols:
            errors.append("duplicate_symbol")
        elif symbol:
            item_symbols.add(symbol)
        errors.extend(
            f"item:{error}"
            for error in recommendation_operation_contract_errors(item)
        )
    publication_gate = payload.get("publication_gate")
    live_claimed = (
        bool(items)
        or payload.get("recommendation_status") == "live_proven"
        or payload.get("live_proof") is True
        or payload.get("evidence_scope") == "live_proof"
        or (
            isinstance(publication_gate, Mapping)
            and publication_gate.get("status") == "allowed"
        )
    )
    if not live_claimed:
        return tuple(errors)
    if (
        payload.get("auto_order") is not False
        and "auto_order" not in errors
    ):
        errors.append("auto_order")
    if not _valid_iso_datetime(payload.get("generated_at")):
        errors.append("generated_at")
    if not _valid_iso_date(payload.get("target_trade_date")):
        errors.append("target_trade_date")
    daily_cap = payload.get("daily_publication_cap")
    profile_gate = payload.get("profile_gate")
    if payload.get("recommendation_status") != "live_proven":
        errors.append("recommendation_status")
    if payload.get("live_proof") is not True:
        errors.append("live_proof")
    if payload.get("evidence_scope") != "live_proof":
        errors.append("evidence_scope")
    if (
        not isinstance(publication_gate, Mapping)
        or publication_gate.get("status") != "allowed"
        or publication_gate.get("reason") is not None
    ):
        errors.append("publication_gate")
    if (
        not isinstance(profile_gate, Mapping)
        or profile_gate.get("live_proof") is not True
        or not _sha256_hex(
            profile_gate.get("evidence_receipt_sha256")
        )
    ):
        errors.append("profile_gate")
    if not _sha256_hex(payload.get("current_pool_audit_sha256")):
        errors.append("current_pool_audit_sha256")
    daily_cap_valid = isinstance(daily_cap, Mapping)
    if daily_cap_valid:
        daily_limit = daily_cap.get("limit")
        prior_symbols = daily_cap.get("prior_symbols")
        prior_count = daily_cap.get("prior_count")
        remaining_before_run = daily_cap.get("remaining_before_run")
        published_this_run = daily_cap.get("published_this_run")
        rejected_this_run = daily_cap.get("rejected_this_run")
        canonical_prior = (
            [
                canonical_recommendation_symbol(symbol)
                for symbol in prior_symbols
            ]
            if isinstance(prior_symbols, list)
            else None
        )
        daily_cap_valid = (
            daily_cap.get("valid") is True
            and isinstance(daily_limit, int)
            and not isinstance(daily_limit, bool)
            and 1 <= daily_limit <= 3
            and canonical_prior is not None
            and all(canonical_prior)
            and canonical_prior == sorted(set(canonical_prior))
            and _nonnegative_int(prior_count)
            and prior_count == len(canonical_prior)
            and _nonnegative_int(remaining_before_run)
            and remaining_before_run
            == max(0, daily_limit - prior_count)
            and _nonnegative_int(published_this_run)
            and published_this_run
            == len(item_symbols - set(canonical_prior))
            and _nonnegative_int(rejected_this_run)
            and prior_count + published_this_run <= daily_limit
            and str(daily_cap.get("target_trade_date") or "")[:10]
            == str(payload.get("target_trade_date") or "")[:10]
        )
    if not daily_cap_valid:
        errors.append("daily_publication_cap")
    if items and require_receipt:
        errors.extend(recommendation_publication_receipt_errors(payload))
    return tuple(errors)


def recommendation_snapshot_sha256(payload: Mapping[str, Any]) -> str:
    return _canonical_sha256(
        {
            key: value
            for key, value in payload.items()
            if key not in {
                "publication_receipt",
                "current_pool_revalidation",
            }
        }
    )


def build_publication_ledger_record(
    *,
    sequence: int,
    generated_at: str,
    target_trade_date: str,
    prior_symbols: set[str],
    published_symbols: set[str],
    previous_record_hash: str | None,
    seeded_from_legacy_history: bool,
    snapshot_sha256: str,
    current_pool_audit_sha256: str,
    profile_evidence_receipt_sha256: str,
) -> dict[str, Any]:
    prior = sorted(
        symbol
        for value in prior_symbols
        if (symbol := canonical_recommendation_symbol(value))
    )
    published = sorted(
        symbol
        for value in published_symbols
        if (symbol := canonical_recommendation_symbol(value))
    )
    payload = {
        "schema_version": PUBLICATION_LEDGER_SCHEMA_VERSION,
        "sequence": int(sequence),
        "generated_at": str(generated_at),
        "target_trade_date": str(target_trade_date)[:10],
        "prior_symbols": prior,
        "published_symbols": published,
        "symbols_after_commit": sorted(set(prior) | set(published)),
        "seeded_from_legacy_history": bool(seeded_from_legacy_history),
        "previous_record_hash": previous_record_hash,
        "snapshot_sha256": str(snapshot_sha256),
        "current_pool_audit_sha256": str(current_pool_audit_sha256),
        "profile_evidence_receipt_sha256": str(
            profile_evidence_receipt_sha256
        ),
    }
    return {
        **payload,
        "record_hash": _canonical_sha256(payload),
    }


def validate_publication_ledger(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, set[str]]:
    states: dict[str, set[str]] = {}
    previous_hash: str | None = None
    for index, value in enumerate(rows, start=1):
        if not isinstance(value, Mapping):
            raise ValueError("recommendation publication ledger row is invalid")
        row = dict(value)
        record_hash = row.pop("record_hash", None)
        if (
            row.get("schema_version")
            != PUBLICATION_LEDGER_SCHEMA_VERSION
            or not isinstance(row.get("sequence"), int)
            or isinstance(row.get("sequence"), bool)
            or row.get("sequence") != index
            or not _valid_iso_datetime(row.get("generated_at"))
            or row.get("previous_record_hash") != previous_hash
            or not _sha256_hex(record_hash)
            or record_hash != _canonical_sha256(row)
            or not _sha256_hex(row.get("snapshot_sha256"))
            or not _sha256_hex(row.get("current_pool_audit_sha256"))
            or not _sha256_hex(
                row.get("profile_evidence_receipt_sha256")
            )
            or not isinstance(row.get("seeded_from_legacy_history"), bool)
        ):
            raise ValueError("recommendation publication ledger chain is invalid")
        target = str(row.get("target_trade_date") or "")[:10]
        if not _valid_iso_date(target):
            raise ValueError("recommendation publication target date is invalid")
        raw_prior = row.get("prior_symbols")
        raw_published = row.get("published_symbols")
        raw_after = row.get("symbols_after_commit")
        if not all(
            isinstance(symbols, list)
            for symbols in (raw_prior, raw_published, raw_after)
        ):
            raise ValueError("recommendation publication symbols are invalid")
        prior = {
            canonical_recommendation_symbol(symbol)
            for symbol in raw_prior
        }
        published = {
            canonical_recommendation_symbol(symbol)
            for symbol in raw_published
        }
        after = {
            canonical_recommendation_symbol(symbol)
            for symbol in raw_after
        }
        if (
            "" in prior
            or "" in published
            or "" in after
            or not published
            or raw_prior != sorted(prior)
            or raw_published != sorted(published)
            or raw_after != sorted(after)
        ):
            raise ValueError("recommendation publication symbol is invalid")
        expected_prior = states.get(target)
        if expected_prior is None:
            if prior and row.get("seeded_from_legacy_history") is not True:
                raise ValueError(
                    "recommendation publication legacy seed is invalid"
                )
        elif (
            prior != expected_prior
            or row.get("seeded_from_legacy_history") is True
        ):
            raise ValueError("recommendation publication prior state drifted")
        if after != prior | published or len(after) > 3:
            raise ValueError("recommendation publication cap is invalid")
        states[target] = after
        previous_hash = str(record_hash)
    return states


def recommendation_publication_receipt_errors(
    payload: Mapping[str, Any],
    ledger_rows: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[str, ...]:
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return ()
    receipt = payload.get("publication_receipt")
    if not isinstance(receipt, Mapping):
        return ("publication_receipt",)
    errors: list[str] = []
    receipt_dict = dict(receipt)
    record_hash = receipt_dict.pop("record_hash", None)
    try:
        expected_record_hash = _canonical_sha256(receipt_dict)
    except (TypeError, ValueError):
        expected_record_hash = None
    if (
        not _sha256_hex(record_hash)
        or record_hash != expected_record_hash
    ):
        errors.append("publication_receipt_hash")
    try:
        expected_snapshot_hash = recommendation_snapshot_sha256(payload)
    except (TypeError, ValueError):
        expected_snapshot_hash = None

    def symbols(value: Any) -> set[str] | None:
        if not isinstance(value, list):
            return None
        result = {
            canonical_recommendation_symbol(symbol)
            for symbol in value
        }
        return result if "" not in result else None

    item_symbols = {
        canonical_recommendation_symbol(item.get("symbol"))
        for item in items
        if isinstance(item, Mapping)
        and canonical_recommendation_symbol(item.get("symbol"))
    }
    prior_symbols = symbols(receipt.get("prior_symbols"))
    published_symbols = symbols(receipt.get("published_symbols"))
    symbols_after_commit = symbols(receipt.get("symbols_after_commit"))
    profile_gate = payload.get("profile_gate")
    profile_receipt_sha256 = (
        profile_gate.get("evidence_receipt_sha256")
        if isinstance(profile_gate, Mapping)
        else None
    )
    daily_cap = payload.get("daily_publication_cap")
    daily_prior = symbols(
        daily_cap.get("prior_symbols")
        if isinstance(daily_cap, Mapping)
        else None
    )
    if (
        receipt.get("schema_version")
        != PUBLICATION_LEDGER_SCHEMA_VERSION
        or receipt.get("generated_at") != payload.get("generated_at")
        or str(receipt.get("target_trade_date") or "")[:10]
        != str(payload.get("target_trade_date") or "")[:10]
        or published_symbols != item_symbols
        or prior_symbols is None
        or published_symbols is None
        or symbols_after_commit is None
        or daily_prior is None
        or prior_symbols != daily_prior
        or symbols_after_commit != prior_symbols | published_symbols
        or len(symbols_after_commit) > 3
        or receipt.get("snapshot_sha256")
        != expected_snapshot_hash
        or receipt.get("current_pool_audit_sha256")
        != payload.get("current_pool_audit_sha256")
        or receipt.get("profile_evidence_receipt_sha256")
        != profile_receipt_sha256
    ):
        errors.append("publication_receipt_binding")
    if ledger_rows is not None:
        try:
            validate_publication_ledger(ledger_rows)
        except (TypeError, ValueError):
            errors.append("publication_ledger")
        else:
            matching_rows = [
                row
                for row in ledger_rows
                if row.get("record_hash") == record_hash
            ]
            target_rows = [
                row
                for row in ledger_rows
                if str(row.get("target_trade_date") or "")[:10]
                == str(payload.get("target_trade_date") or "")[:10]
            ]
            if (
                len(matching_rows) != 1
                or dict(matching_rows[0]) != dict(receipt)
                or not target_rows
                or target_rows[-1].get("record_hash") != record_hash
            ):
                errors.append("publication_receipt_not_in_ledger")
    return tuple(dict.fromkeys(errors))
