from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum

from poma.models import OpenOrderSnapshot, OrderResult, OrderSide

ORDER_REF_PREFIX = "poma"
EXECUTION_QUOTE_BLOCKED_STATUS = "QuoteBlocked"


class OrderLifecycleState(StrEnum):
    """Internal lifecycle for one order, independent of the broker's raw status string.

    Raw IBKR status is always kept alongside this (``OrderLedgerEntry.raw_status``) so nothing
    is lost, but downstream policy (replace/cancel, stale-session checks) reasons about this
    smaller, stable set of states instead of broker-specific strings.
    """

    PLANNED = "planned"
    SUBMITTED = "submitted"
    BROKER_ACCEPTED = "broker_accepted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    REPLACE_PENDING = "replace_pending"
    CANCEL_PENDING = "cancel_pending"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


TERMINAL_LIFECYCLE_STATES = frozenset(
    {
        OrderLifecycleState.FILLED,
        OrderLifecycleState.CANCELLED,
        OrderLifecycleState.REJECTED,
        OrderLifecycleState.EXPIRED,
    }
)

WORKING_LIFECYCLE_STATES = frozenset(
    {
        OrderLifecycleState.BROKER_ACCEPTED,
        OrderLifecycleState.PARTIALLY_FILLED,
    }
)

_RAW_TERMINAL_CANCELLED = {"Cancelled", "ApiCancelled"}
_RAW_SUBMISSION_FAILURE = {"Failed", "BrokerUnavailable", "OrderNotAccepted", EXECUTION_QUOTE_BLOCKED_STATUS}


def build_order_ref(run_id: str, index: int, ticker: str, side: OrderSide) -> str:
    """Deterministic, IBKR-safe orderRef so a reconnect can recognize an already-placed order."""
    return f"{ORDER_REF_PREFIX}:{run_id}:{index}:{ticker}:{side.value}"


def classify_lifecycle(raw_status: str, filled: float, remaining: float) -> OrderLifecycleState:
    """Map a broker raw status plus fill state to the internal lifecycle enum."""
    status = raw_status or ""
    if status == "Filled" or (filled > 0 and remaining <= 1e-9):
        return OrderLifecycleState.FILLED
    if status in _RAW_TERMINAL_CANCELLED:
        return OrderLifecycleState.CANCELLED
    if status == "PendingCancel":
        return OrderLifecycleState.CANCEL_PENDING
    if status == "Inactive" or status in _RAW_SUBMISSION_FAILURE:
        return OrderLifecycleState.REJECTED
    if status in {"PreSubmitted", "Submitted"}:
        return OrderLifecycleState.PARTIALLY_FILLED if filled > 0 else OrderLifecycleState.BROKER_ACCEPTED
    if status == "PendingSubmit":
        return OrderLifecycleState.SUBMITTED
    return OrderLifecycleState.UNKNOWN


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def seconds_since(iso_timestamp: str | None, now: datetime) -> float | None:
    if not iso_timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(iso_timestamp)
    except ValueError:
        return None
    return (now - parsed).total_seconds()


def more_aggressive_limit_price(side: OrderSide, limit_price: float, improvement_bps: float) -> float:
    """A slightly more aggressive limit price, used for the single allowed replace attempt."""
    multiplier = 1 + improvement_bps / 10_000 if side == OrderSide.BUY else 1 - improvement_bps / 10_000
    return round(limit_price * multiplier, 2)


@dataclass(frozen=True)
class OrderLedgerEntry:
    """Durable record of one order's lifecycle, independent of any single process run.

    ``ledger_key`` is the stable identity for this order across its lifetime (including any
    replace); ``order_ref`` is the orderRef of the order currently live at the broker, which
    changes to a new value after a replace.
    """

    ledger_key: str
    order_ref: str
    run_id: str
    session_date: str
    ticker: str
    side: OrderSide
    quantity: float
    limit_price: float | None
    strategy: str | None = None
    reference_price: float | None = None
    reference_price_source: str | None = None
    reference_price_basis: str | None = None
    reference_price_as_of_utc: str | None = None
    quote_age_seconds: float | None = None
    quote_spread_bps: float | None = None
    order_id: int | None = None
    perm_id: int | None = None
    submitted_at: str | None = None
    lifecycle_state: OrderLifecycleState = OrderLifecycleState.PLANNED
    raw_status: str = ""
    filled_qty: float = 0.0
    remaining_qty: float = 0.0
    avg_fill_price: float | None = None
    last_status_at: str | None = None
    terminal_reason: str | None = None
    replace_count: int = 0

    @property
    def is_terminal(self) -> bool:
        return self.lifecycle_state in TERMINAL_LIFECYCLE_STATES

    def with_order_result(self, result: OrderResult) -> OrderLedgerEntry:
        remaining = max(self.quantity - result.filled, 0.0)
        lifecycle = classify_lifecycle(result.status, result.filled, remaining)
        now_iso = _utc_now_iso()
        return replace(
            self,
            order_id=result.order_id if result.order_id is not None else self.order_id,
            submitted_at=self.submitted_at or now_iso,
            lifecycle_state=lifecycle,
            raw_status=result.status,
            filled_qty=result.filled,
            remaining_qty=remaining,
            avg_fill_price=result.average_fill_price if result.average_fill_price is not None else self.avg_fill_price,
            last_status_at=now_iso,
            terminal_reason=(result.message if lifecycle in TERMINAL_LIFECYCLE_STATES else self.terminal_reason),
        )

    def with_snapshot(self, snapshot: OpenOrderSnapshot) -> OrderLedgerEntry:
        remaining = max(snapshot.remaining, 0.0)
        lifecycle = classify_lifecycle(snapshot.raw_status, snapshot.filled, remaining)
        return replace(
            self,
            order_id=snapshot.order_id if snapshot.order_id is not None else self.order_id,
            perm_id=snapshot.perm_id if snapshot.perm_id is not None else self.perm_id,
            lifecycle_state=lifecycle,
            raw_status=snapshot.raw_status,
            filled_qty=snapshot.filled,
            remaining_qty=remaining,
            avg_fill_price=snapshot.avg_fill_price if snapshot.avg_fill_price is not None else self.avg_fill_price,
            last_status_at=_utc_now_iso(),
            terminal_reason=(
                f"broker reported {snapshot.raw_status}"
                if lifecycle in TERMINAL_LIFECYCLE_STATES
                else self.terminal_reason
            ),
        )

    def to_json(self) -> dict[str, object]:
        payload = asdict(self)
        payload["side"] = self.side.value
        payload["lifecycle_state"] = self.lifecycle_state.value
        return payload

    @classmethod
    def from_json(cls, payload: dict[str, object]) -> OrderLedgerEntry:
        data = dict(payload)
        data["side"] = OrderSide(data["side"])
        data["lifecycle_state"] = OrderLifecycleState(data["lifecycle_state"])
        return cls(**data)
