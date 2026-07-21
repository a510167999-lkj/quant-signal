"""Read-only composition primitives for independently audited PIT artifacts.

This module deliberately does not integrate with the historical replay entrypoint.
It defines the fail-closed authority, routing, and price-rebasing boundary that the
entrypoint can consume without pretending several artifacts are one artifact.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import math
import os
import stat
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence


COMPOSITE_UNIVERSE_SCHEMA_VERSION = "research-composite-universe/v1"
COMPOSITE_DESCRIPTOR_SCHEMA_VERSION = "research-composite-universe-descriptor/v1"


class CompositeUniverseError(ValueError):
    """Raised when segmented evidence cannot form one fail-closed composite."""


def _iso_date(value: Any, label: str) -> str:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError) as exc:
        raise CompositeUniverseError(f"{label} is not an ISO date") from exc


def _sha256(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_sha256(value: Any, label: str) -> str:
    normalized = str(value or "").strip()
    if len(normalized) != 64 or any(character not in "0123456789abcdefABCDEF" for character in normalized):
        raise CompositeUniverseError(f"{label} is not a SHA-256 digest")
    return normalized


def _segment_authority(universe: Any, sequence: int) -> dict[str, Any]:
    if getattr(universe, "is_audited_store_artifact", False) is not True:
        raise CompositeUniverseError("segment is not an audited store artifact")
    if getattr(universe, "external_temporal_authority_verified", False) is not True:
        raise CompositeUniverseError("segment external temporal authority is not verified")
    if getattr(universe, "final_oos_eligible", None) is not False:
        raise CompositeUniverseError("composite rejects a final-OOS-eligible segment")

    start = _iso_date(getattr(universe, "start_date", None), "segment start_date")
    end = _iso_date(getattr(universe, "end_date", None), "segment end_date")
    if end < start:
        raise CompositeUniverseError("segment end_date precedes start_date")
    role = str(getattr(universe, "temporal_role", "") or "").strip()
    if not role:
        raise CompositeUniverseError("segment temporal role is missing")
    contract = _require_sha256(
        getattr(universe, "temporal_contract_sha256", None),
        "segment temporal contract",
    )
    manifest = getattr(universe, "manifest", None)
    if not isinstance(manifest, Mapping):
        raise CompositeUniverseError("segment artifact manifest is missing")
    market = manifest.get("market_generations")
    stock = manifest.get("stock_generation")
    if not isinstance(market, Mapping) or not isinstance(stock, Mapping):
        raise CompositeUniverseError("segment artifact manifest authority is incomplete")

    return {
        "sequence": sequence,
        "coverage": {"start_date": start, "end_date": end},
        "artifact_root_sha256": _require_sha256(
            getattr(universe, "artifact_root_sha256", None), "segment artifact root"
        ),
        "coverage_audit_sha256": _require_sha256(
            getattr(universe, "coverage_audit_sha256", None), "segment coverage audit"
        ),
        "temporal_contract_sha256": contract,
        "temporal_role": role,
        "artifact_manifest_sha256": _require_sha256(
            manifest.get("manifest_sha256"), "segment artifact manifest"
        ),
        "market_generation_root_sha256": _require_sha256(
            market.get("root_sha256"), "segment market generation root"
        ),
        "stock_generation_lineage_sha256": _require_sha256(
            stock.get("lineage_sha256"), "segment stock generation lineage"
        ),
    }


class CompositeAuditedUniverse:
    """Ordered, non-promotable view over several independently audited segments."""

    is_composite_audited_universe = True
    is_audited_store_artifact = True
    final_oos_eligible = False

    def __init__(
        self,
        segments: Sequence[Any],
        *,
        permitted_boundary_gaps: Sequence[tuple[str, str]] = (),
    ) -> None:
        if not isinstance(segments, Sequence) or isinstance(segments, (str, bytes)):
            raise CompositeUniverseError("segments must be an ordered sequence")
        if not segments:
            raise CompositeUniverseError("composite requires at least one segment")
        self._segments = tuple(segments)
        authorities = tuple(
            _segment_authority(universe, sequence)
            for sequence, universe in enumerate(self._segments, 1)
        )

        gap_pairs: list[tuple[str, str]] = []
        for raw_pair in permitted_boundary_gaps:
            if not isinstance(raw_pair, (tuple, list)) or len(raw_pair) != 2:
                raise CompositeUniverseError("permitted boundary gap is invalid")
            pair = (
                _iso_date(raw_pair[0], "boundary previous end_date"),
                _iso_date(raw_pair[1], "boundary next start_date"),
            )
            if pair in gap_pairs:
                raise CompositeUniverseError("permitted boundary gaps contain a duplicate")
            gap_pairs.append(pair)
        permitted = set(gap_pairs)
        used_gaps: set[tuple[str, str]] = set()

        for previous, current in zip(authorities, authorities[1:]):
            previous_start = previous["coverage"]["start_date"]
            previous_end = previous["coverage"]["end_date"]
            current_start = current["coverage"]["start_date"]
            if current_start <= previous_start:
                raise CompositeUniverseError("segments must be ordered by start_date")
            if current_start <= previous_end:
                raise CompositeUniverseError("segment coverage overlap is forbidden")
            adjacent = (
                date.fromisoformat(previous_end) + timedelta(days=1)
            ).isoformat()
            if current_start != adjacent:
                boundary = (previous_end, current_start)
                if boundary not in permitted:
                    raise CompositeUniverseError(
                        "segment coverage gap lacks an explicit boundary attestation"
                    )
                used_gaps.add(boundary)

        unused_gaps = permitted - used_gaps
        if unused_gaps:
            raise CompositeUniverseError(
                "permitted boundary gap does not match an actual segment boundary"
            )

        roles = {authority["temporal_role"] for authority in authorities}
        if len(roles) != 1:
            raise CompositeUniverseError("segments have different temporal roles")
        contracts = {
            authority["temporal_contract_sha256"] for authority in authorities
        }
        if len(contracts) != 1:
            raise CompositeUniverseError("segments have different temporal contracts")

        self._authorities = authorities
        self._component_hashes = tuple(
            {
                "artifact_root_sha256": authority["artifact_root_sha256"],
                "calendar_sha256": _require_sha256(
                    getattr(universe, "calendar_sha256", None),
                    "segment calendar",
                ),
                "source_manifest_sha256": _require_sha256(
                    getattr(universe, "source_manifest_sha256", None),
                    "segment source manifest",
                ),
                "coverage_audit_sha256": authority["coverage_audit_sha256"],
            }
            for universe, authority in zip(self._segments, authorities)
        )
        self._permitted_boundary_gaps = tuple(gap_pairs)
        unsigned_authority = {
            "schema_version": COMPOSITE_UNIVERSE_SCHEMA_VERSION,
            "coverage": {
                "start_date": authorities[0]["coverage"]["start_date"],
                "end_date": authorities[-1]["coverage"]["end_date"],
            },
            "temporal_role": authorities[0]["temporal_role"],
            "temporal_contract_sha256": authorities[0][
                "temporal_contract_sha256"
            ],
            "permitted_boundary_gaps": [
                {
                    "previous_end_date": previous_end,
                    "next_start_date": next_start,
                }
                for previous_end, next_start in gap_pairs
            ],
            "segments": list(authorities),
        }
        self._composite_root_sha256 = _sha256(unsigned_authority)
        self._composite_authority = {
            **unsigned_authority,
            "composite_root_sha256": self._composite_root_sha256,
        }

    @property
    def composite_root_sha256(self) -> str:
        return self._composite_root_sha256

    @property
    def artifact_root_sha256(self) -> str:
        """Return the root of the ordered composite authority, not a segment root."""

        return self._composite_root_sha256

    @property
    def universe_sha256(self) -> str:
        return self._composite_root_sha256

    def _component_root(self, schema_version: str, field: str) -> str:
        return _sha256(
            {
                "schema_version": schema_version,
                "segments": [
                    {
                        "artifact_root_sha256": row["artifact_root_sha256"],
                        field: row[field],
                    }
                    for row in self._component_hashes
                ],
            }
        )

    @property
    def calendar_sha256(self) -> str:
        return self._component_root(
            "research-composite-calendar/v1", "calendar_sha256"
        )

    @property
    def source_manifest_sha256(self) -> str:
        return self._component_root(
            "research-composite-source-manifest/v1", "source_manifest_sha256"
        )

    @property
    def coverage_audit_sha256(self) -> str:
        return self._component_root(
            "research-composite-coverage-audit/v1", "coverage_audit_sha256"
        )

    @property
    def external_temporal_authority_verified(self) -> bool:
        """Every constituent authority was verified before construction."""

        return True

    @property
    def composite_authority(self) -> dict[str, Any]:
        return copy.deepcopy(self._composite_authority)

    @property
    def verified_segments(self) -> tuple[Any, ...]:
        """Expose the already verified read-only segments in authority order."""

        return self._segments

    @property
    def start_date(self) -> str:
        return self._authorities[0]["coverage"]["start_date"]

    @property
    def end_date(self) -> str:
        return self._authorities[-1]["coverage"]["end_date"]

    @property
    def temporal_role(self) -> str:
        return self._authorities[0]["temporal_role"]

    @property
    def temporal_contract_sha256(self) -> str:
        return self._authorities[0]["temporal_contract_sha256"]

    def _require_open(self) -> "CompositeAuditedUniverse":
        """Fail before replay if any constituent read-only snapshot is closed."""

        for segment in self._segments:
            require_open = getattr(segment, "_require_open", None)
            if not callable(require_open):
                raise CompositeUniverseError(
                    "audited segment does not expose the open-state guard"
                )
            require_open()
        return self

    def segment_for_date(self, value: Any) -> Any:
        session = _iso_date(value, "routed date")
        owners = [
            universe
            for universe, authority in zip(self._segments, self._authorities)
            if authority["coverage"]["start_date"]
            <= session
            <= authority["coverage"]["end_date"]
        ]
        if len(owners) != 1:
            raise CompositeUniverseError(
                f"date is not owned by exactly one audited segment: {session}"
            )
        return owners[0]

    def items_as_of(self, signal_date: Any) -> list[dict[str, Any]]:
        session = _iso_date(signal_date, "signal_date")
        return self.segment_for_date(session).items_as_of(session)

    def item_as_of(self, symbol: Any, signal_date: Any) -> Any:
        session = _iso_date(signal_date, "signal_date")
        return self.segment_for_date(session).item_as_of(str(symbol), session)

    def next_open_execution_evidence(
        self, symbol: Any, trade_date: Any, side: Any
    ) -> dict[str, Any]:
        session = _iso_date(trade_date, "trade_date")
        return self.segment_for_date(session).next_open_execution_evidence(
            str(symbol), session, str(side)
        )

    def open_sessions(self, start_date: Any, end_date: Any) -> list[str]:
        """Return the ordered union of covered open sessions across segments."""

        first = _iso_date(start_date, "start_date")
        last = _iso_date(end_date, "end_date")
        if last < first:
            raise CompositeUniverseError("end_date precedes start_date")
        if first < self.start_date or last > self.end_date:
            raise CompositeUniverseError(
                "requested session range is outside composite coverage"
            )

        sessions: list[str] = []
        for segment, authority in zip(self._segments, self._authorities):
            segment_start = authority["coverage"]["start_date"]
            segment_end = authority["coverage"]["end_date"]
            clipped_start = max(first, segment_start)
            clipped_end = min(last, segment_end)
            if clipped_start > clipped_end:
                continue
            rows = segment.open_sessions(clipped_start, clipped_end)
            if not isinstance(rows, list):
                raise CompositeUniverseError(
                    "segment open_sessions result must be a list"
                )
            for raw_session in rows:
                session = _iso_date(raw_session, "open session")
                if not clipped_start <= session <= clipped_end:
                    raise CompositeUniverseError(
                        "segment returned an open session outside its routed range"
                    )
                if self.segment_for_date(session) is not segment:
                    raise CompositeUniverseError(
                        "open session was returned by the wrong segment"
                    )
                sessions.append(session)
        if sessions != sorted(set(sessions)):
            raise CompositeUniverseError(
                "composite open sessions are duplicated or unsorted"
            )
        return sessions

    def causal_signal_bars(
        self, symbol: Any, start_date: Any, as_of_date: Any
    ) -> list[dict[str, Any]]:
        """Read every routed segment and normalize all signal prices globally."""

        start = _iso_date(start_date, "start_date")
        as_of = _iso_date(as_of_date, "as_of_date")
        if as_of < start:
            raise CompositeUniverseError("as_of_date precedes start_date")
        # Explicit boundary attestations permit closed calendar gaps, not reads
        # whose endpoints fall outside every segment.
        self.segment_for_date(start)
        self.segment_for_date(as_of)

        batches: list[list[dict[str, Any]]] = []
        normalized_symbol = str(symbol)
        for segment, authority in zip(self._segments, self._authorities):
            segment_start = authority["coverage"]["start_date"]
            segment_end = authority["coverage"]["end_date"]
            clipped_start = max(start, segment_start)
            clipped_as_of = min(as_of, segment_end)
            if clipped_start > clipped_as_of:
                continue
            rows = segment.causal_signal_bars(
                normalized_symbol, clipped_start, clipped_as_of
            )
            if not isinstance(rows, list):
                raise CompositeUniverseError(
                    "segment causal_signal_bars result must be a list"
                )
            batches.append(rows)
        return rebase_segmented_causal_bars(batches, as_of_date=as_of)

    def seed_items(self, start_date: Any, end_date: Any) -> list[dict[str, Any]]:
        """Return a deterministic first-seen union of segment seed symbols."""

        first = _iso_date(start_date, "start_date")
        last = _iso_date(end_date, "end_date")
        if last < first:
            raise CompositeUniverseError("end_date precedes start_date")
        if first < self.start_date or last > self.end_date:
            raise CompositeUniverseError("seed range is outside composite coverage")

        by_symbol: dict[str, dict[str, Any]] = {}
        for segment, authority in zip(self._segments, self._authorities):
            clipped_start = max(first, authority["coverage"]["start_date"])
            clipped_end = min(last, authority["coverage"]["end_date"])
            if clipped_start > clipped_end:
                continue
            rows = segment.seed_items(clipped_start, clipped_end)
            if not isinstance(rows, list):
                raise CompositeUniverseError("segment seed_items result must be a list")
            for row in rows:
                if not isinstance(row, Mapping):
                    raise CompositeUniverseError("segment seed item must be a mapping")
                normalized = dict(row)
                symbol = str(normalized.get("symbol") or "").strip()
                if not symbol:
                    raise CompositeUniverseError("segment seed item has no symbol")
                by_symbol.setdefault(symbol, normalized)
        return list(by_symbol.values())

    def close(self) -> None:
        """Close every constituent snapshot, attempting all segments."""

        first_error: Exception | None = None
        for segment in self._segments:
            try:
                segment.close()
            except Exception as exc:  # pragma: no cover - defensive resource cleanup
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error


def _read_composite_descriptor_snapshot(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(str(path), flags)
    except OSError as exc:
        raise CompositeUniverseError("composite descriptor is unreadable") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size < 1:
            raise CompositeUniverseError("composite descriptor is unreadable")
        if before.st_size > 64 * 1024:
            raise CompositeUniverseError("composite descriptor exceeds size limit")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        if (
            len(raw) != before.st_size
            or after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
        ):
            raise CompositeUniverseError("composite descriptor changed while reading")
        return raw
    finally:
        os.close(descriptor)


def load_composite_universe_descriptor(
    descriptor_path: str,
    *,
    expected_composite_root_sha256: str,
    expected_descriptor_file_sha256: str = None,
) -> CompositeAuditedUniverse:
    """Open all descriptor segments through their externally anchored loader."""

    from app.research_pit_store import AuditedPointInTimeUniverse

    expected_root = _require_sha256(
        expected_composite_root_sha256, "expected composite root"
    )
    expected_descriptor = (
        _require_sha256(
            expected_descriptor_file_sha256, "expected composite descriptor file"
        )
        if expected_descriptor_file_sha256 is not None
        else None
    )
    path = Path(str(descriptor_path)).expanduser().resolve()
    try:
        raw = _read_composite_descriptor_snapshot(path)
        if expected_descriptor is not None and not hmac.compare_digest(
            hashlib.sha256(raw).hexdigest(), expected_descriptor
        ):
            raise CompositeUniverseError("composite descriptor external hash mismatch")
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompositeUniverseError("composite descriptor is unreadable") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "segments",
        "permitted_boundary_gaps",
    }:
        raise CompositeUniverseError("composite descriptor keys are invalid")
    if payload["schema_version"] != COMPOSITE_DESCRIPTOR_SCHEMA_VERSION:
        raise CompositeUniverseError("unsupported composite descriptor schema")
    segment_descriptors = payload["segments"]
    if not isinstance(segment_descriptors, list) or not segment_descriptors:
        raise CompositeUniverseError("composite descriptor segments are invalid")
    raw_gaps = payload["permitted_boundary_gaps"]
    if not isinstance(raw_gaps, list):
        raise CompositeUniverseError("composite descriptor boundary gaps are invalid")
    permitted_gaps: list[tuple[str, str]] = []
    for gap in raw_gaps:
        if not isinstance(gap, dict) or set(gap) != {
            "previous_end_date",
            "next_start_date",
        }:
            raise CompositeUniverseError(
                "composite descriptor boundary gap is invalid"
            )
        permitted_gaps.append(
            (str(gap["previous_end_date"]), str(gap["next_start_date"]))
        )

    segment_keys = {
        "path",
        "expected_artifact_root_sha256",
        "expected_coverage_audit_sha256",
        "expected_temporal_contract_sha256",
        "expected_artifact_manifest_sha256",
        "expected_temporal_role",
    }
    opened: list[Any] = []
    try:
        for descriptor in segment_descriptors:
            if not isinstance(descriptor, dict) or set(descriptor) != segment_keys:
                raise CompositeUniverseError(
                    "composite descriptor segment keys are invalid"
                )
            raw_segment_path = Path(str(descriptor["path"]))
            segment_path = (
                raw_segment_path
                if raw_segment_path.is_absolute()
                else path.parent / raw_segment_path
            ).resolve()
            expected_manifest = _require_sha256(
                descriptor["expected_artifact_manifest_sha256"],
                "expected segment artifact manifest",
            )
            universe = AuditedPointInTimeUniverse.from_file(
                str(segment_path),
                expected_coverage_audit_sha256=_require_sha256(
                    descriptor["expected_coverage_audit_sha256"],
                    "expected segment coverage audit",
                ),
                expected_artifact_root_sha256=_require_sha256(
                    descriptor["expected_artifact_root_sha256"],
                    "expected segment artifact root",
                ),
                expected_temporal_contract_sha256=_require_sha256(
                    descriptor["expected_temporal_contract_sha256"],
                    "expected segment temporal contract",
                ),
                expected_temporal_role=str(descriptor["expected_temporal_role"]),
            )
            opened.append(universe)
            actual_manifest = str(universe.manifest.get("manifest_sha256") or "")
            if not hmac.compare_digest(actual_manifest, expected_manifest):
                raise CompositeUniverseError(
                    "segment artifact manifest does not match external anchor"
                )
        composite = CompositeAuditedUniverse(
            opened, permitted_boundary_gaps=permitted_gaps
        )
        if not hmac.compare_digest(
            composite.composite_root_sha256, expected_root
        ):
            raise CompositeUniverseError(
                "composite root does not match external anchor"
            )
        return composite
    except Exception:
        for universe in opened:
            try:
                universe.close()
            except Exception:
                pass
        raise


def _positive_finite(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise CompositeUniverseError(f"{label} is not numeric") from exc
    if not math.isfinite(number) or number <= 0:
        raise CompositeUniverseError(f"{label} must be finite and positive")
    return number


def rebase_segmented_causal_bars(
    segment_rows: Sequence[Sequence[Mapping[str, Any]]],
    *,
    as_of_date: Any,
) -> list[dict[str, Any]]:
    """Rebase one symbol's segmented raw bars onto one global causal factor.

    Each input segment may have normalized its signal OHLC using a different
    local as-of factor. The function discards those local signal values and
    deterministically rebuilds them from ``raw_*`` and ``bar_adj_factor`` using
    the latest factor date present across all segments on or before ``as_of``.
    """

    as_of = _iso_date(as_of_date, "as_of_date")
    flattened: list[dict[str, Any]] = []
    seen_dates: set[str] = set()
    symbols: set[str] = set()
    anchors: dict[str, float] = {}

    for rows in segment_rows:
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            raise CompositeUniverseError("segment causal bars must be a sequence")
        for source in rows:
            if not isinstance(source, Mapping):
                raise CompositeUniverseError("causal bar row must be a mapping")
            row = dict(source)
            symbol = str(row.get("ts_code") or "").strip()
            if not symbol:
                raise CompositeUniverseError("causal bar symbol is missing")
            symbols.add(symbol)
            session = _iso_date(row.get("trade_date"), "causal bar trade_date")
            if session > as_of:
                raise CompositeUniverseError("causal bar exceeds global as_of_date")
            if session in seen_dates:
                raise CompositeUniverseError("duplicate causal bar trade_date")
            seen_dates.add(session)
            adjustment_date = _iso_date(
                row.get("adjustment_as_of_date"), "adjustment_as_of_date"
            )
            if adjustment_date > as_of:
                raise CompositeUniverseError(
                    "adjustment anchor exceeds global as_of_date"
                )
            anchor_factor = _positive_finite(
                row.get("as_of_adj_factor"), "as-of adjustment factor"
            )
            previous_anchor = anchors.get(adjustment_date)
            if previous_anchor is not None and not math.isclose(
                previous_anchor, anchor_factor, rel_tol=1e-12, abs_tol=1e-12
            ):
                raise CompositeUniverseError(
                    "adjustment anchor has inconsistent factors"
                )
            anchors[adjustment_date] = anchor_factor
            row["trade_date"] = session
            flattened.append(row)

    if not flattened:
        return []
    if len(symbols) != 1:
        raise CompositeUniverseError("segmented causal bars must contain one symbol")

    global_adjustment_date = max(anchors)
    global_factor = anchors[global_adjustment_date]
    normalized: list[dict[str, Any]] = []
    for row in flattened:
        bar_factor = _positive_finite(
            row.get("bar_adj_factor"), "bar adjustment factor"
        )
        ratio = bar_factor / global_factor
        rebuilt = dict(row)
        for field in ("open", "high", "low", "close"):
            raw_value = _positive_finite(row.get(f"raw_{field}"), f"raw {field}")
            rebuilt[f"signal_{field}"] = raw_value * ratio
        rebuilt["as_of_adj_factor"] = global_factor
        rebuilt["adjustment_as_of_date"] = global_adjustment_date
        normalized.append(rebuilt)

    return sorted(normalized, key=lambda row: row["trade_date"])
