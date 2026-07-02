from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from ib_insync import IB, LimitOrder, MarketOrder, Stock, Trade

from poma.config import ExecutionPriceSource, OrderType, Settings, TradingMode
from poma.execution_pricing import compute_spread_bps
from poma.models import (
    AccountSnapshot,
    CurrentPosition,
    ExecutionQuote,
    OpenOrderSnapshot,
    OrderResult,
    OrderSide,
    ProposedTrade,
)
from poma.order_lifecycle import IDEMPOTENT_REPLAY_STATUS, ORDER_REF_PREFIX

# ib_insync reports market data type as an int on the ticker/reqMarketDataType callback:
# 1=live, 2=frozen, 3=delayed, 4=delayed-frozen. Only live/frozen reflect real-time prices:
# frozen serves the last real-time quote when the market is closed and requires the same
# entitlement as live, so a frozen tick proves real-time entitlement outside market hours.
MARKET_DATA_TYPE_NAMES = {1: "live", 2: "frozen", 3: "delayed", 4: "delayed_frozen"}
DELAYED_MARKET_DATA_TYPES = {"delayed", "delayed_frozen"}
LIVE_MARKET_DATA_TYPE = 1
FROZEN_MARKET_DATA_TYPE = 2
DELAYED_MARKET_DATA_TYPE = 3
DELAYED_FROZEN_MARKET_DATA_TYPE = 4
REALTIME_MARKET_DATA_TYPE_CODES = {LIVE_MARKET_DATA_TYPE, FROZEN_MARKET_DATA_TYPE}
FROZEN_MARKET_DATA_TYPE_CODES = {FROZEN_MARKET_DATA_TYPE, DELAYED_FROZEN_MARKET_DATA_TYPE}
DELAYED_MARKET_DATA_TYPE_CODES = {DELAYED_MARKET_DATA_TYPE, DELAYED_FROZEN_MARKET_DATA_TYPE}
# The readiness probe walks every market data type so an off-hours check is still conclusive:
# live first, then frozen (after-hours real-time evidence), then the delayed variants. The
# ladder is probe-only; execution pricing never reads frozen data.
PROBE_MARKET_DATA_TYPE_LADDER = (
    LIVE_MARKET_DATA_TYPE,
    FROZEN_MARKET_DATA_TYPE,
    DELAYED_MARKET_DATA_TYPE,
    DELAYED_FROZEN_MARKET_DATA_TYPE,
)
# Delayed-data subscriptions can take noticeably longer than live ones to start ticking,
# especially right after a Gateway restart before market data farm connections have settled.
# A too-short wait here reads identically to "no entitlement at all" (no tick, no error), so err
# generous: this runs once per rebalance/probe, not per symbol.
EXECUTION_QUOTE_WAIT_SECONDS = 5.0
DELAYED_PROBE_WAIT_MULTIPLIER = 2

DONE_STATUSES = {"Filled", "Cancelled", "ApiCancelled", "Inactive"}
ACCEPTED_STATUSES = {"PreSubmitted", "Submitted", "Filled"}
SUCCESS_STATUSES = ACCEPTED_STATUSES
BROKER_UNAVAILABLE_STATUS = "BrokerUnavailable"
ORDER_NOT_ACCEPTED_STATUS = "OrderNotAccepted"
TRADING_PERMISSION_PROBE_SYMBOL = "AAPL"
TRADING_PERMISSION_PROBE_LIMIT_PRICE = 1.0
MARKET_DATA_PROBE_SYMBOL = TRADING_PERMISSION_PROBE_SYMBOL
USD_CASH_TAGS = ("TotalCashValue", "TotalCashBalance", "CashBalance", "SettledCash")
NET_LIQUIDATION_TAGS = ("NetLiquidation", "NetLiquidationByCurrency")
GROSS_POSITION_VALUE_TAGS = ("GrossPositionValue",)

# Health probes use a dedicated client id offset so they never collide with the client id the
# scheduled trader connects with (a duplicate client id is rejected by the gateway).
HEALTH_CLIENT_ID_OFFSET = 90
IBKR_CONNECT_TIMEOUT_SECONDS = 20.0
OrderStatusCallback = Callable[[ProposedTrade, OrderResult], None]


class BrokerUnavailable(RuntimeError):
    """Raised when the IBKR API is not ready enough to safely submit orders."""


@dataclass(frozen=True)
class IbkrHealth:
    connected: bool
    accounts: list[str]
    server_time: str
    stock_positions: int
    trading_permissions_ok: bool
    trading_permissions_message: str
    market_data_ok: bool
    market_data_message: str
    # Which market data type actually ticked ("live"/"frozen"/"delayed"/"delayed_frozen"), or
    # None when nothing ticked (or the probe was skipped). realtime is True only for live/frozen
    # -- the direct answer to "does this session have real-time entitlement". A soft failure is
    # an inconclusive probe (market closed, no tick, no broker error) that must not fail configure.
    market_data_type: str | None = None
    market_data_realtime: bool = False
    market_data_soft_failure: bool = False


@dataclass
class _MarketDataErrors:
    """IBKR API error/warning callbacks captured while market data was being requested."""

    per_symbol: dict[str, list[str]] = field(default_factory=dict)
    general: list[str] = field(default_factory=list)


@contextmanager
def _collect_market_data_errors(ib: IB) -> Iterator[_MarketDataErrors]:
    """Capture ``ib.errorEvent`` callbacks fired while a market data request is outstanding.

    ib_insync logs these (e.g. error 354 "not subscribed", warning 10167 "delayed market data
    not available", 2103-2108 market data farm connection status) without raising, so a ticker
    that never receives a tick looks identical whether the account lacks entitlements, the data
    farm connection is down, or the request is merely still warming up. Surfacing the raw IBKR
    text turns that silence into an actionable reason.
    """
    errors = _MarketDataErrors()

    def on_error(_req_id: int, error_code: int, error_string: str, contract: object) -> None:
        message = f"{error_code}: {error_string}"
        symbol = getattr(contract, "symbol", None) if contract is not None else None
        if symbol:
            errors.per_symbol.setdefault(str(symbol).upper(), []).append(message)
        else:
            errors.general.append(message)

    ib.errorEvent += on_error
    try:
        yield errors
    finally:
        ib.errorEvent -= on_error


def _connect_ib(settings: Settings, *, client_id: int, timeout: float) -> IB:
    """Connect to IBKR and explicitly request live market data on this session.

    Gateway remembers whichever market data type was last requested on a client id rather than
    defaulting to live every session, so a fresh connection can otherwise silently return no
    ticks at all even with live entitlements in place.
    """
    ib = IB()
    try:
        ib.connect(
            settings.ibkr_host,
            settings.ibkr_port,
            clientId=client_id,
            account=settings.ibkr_account or "",
            timeout=timeout,
        )
        ib.RequestTimeout = timeout
        ib.reqMarketDataType(LIVE_MARKET_DATA_TYPE)
    except Exception:
        ib.disconnect()
        raise
    return ib


def _retry_missing_quotes_as_delayed(ib: IB, market_data_by_ticker: dict[str, object], *, allow_delayed: bool) -> None:
    """Re-request, as delayed data, any ticker that got no tick at all from the live request.

    `reqMarketDataType` only affects subsequent requests, so tickers that already received a
    live tick keep it; only the ones with no tick are cancelled and re-subscribed under delayed
    data. Selection still enforces `ALLOW_DELAYED_EXECUTION_QUOTES` per quote, so a symbol
    without even delayed data available still blocks execution as before.
    """
    if not allow_delayed:
        return
    missing_tickers = [
        ticker
        for ticker, market_data in market_data_by_ticker.items()
        if not isinstance(getattr(market_data, "time", None), datetime)
    ]
    if not missing_tickers:
        return
    for ticker in missing_tickers:
        ib.cancelMktData(getattr(market_data_by_ticker[ticker], "contract"))  # noqa: B009
    ib.reqMarketDataType(DELAYED_MARKET_DATA_TYPE)
    for ticker in missing_tickers:
        market_data_by_ticker[ticker] = ib.reqMktData(Stock(ticker, "SMART", "USD"), "", False, False)
    # Delayed subscriptions take noticeably longer than live ones to produce a first tick (see
    # DELAYED_PROBE_WAIT_MULTIPLIER); a live-sized wait here reads as "still no quote" and blocks
    # every fallback ticker even though delayed data was about to arrive.
    ib.sleep(EXECUTION_QUOTE_WAIT_SECONDS * DELAYED_PROBE_WAIT_MULTIPLIER)
    ib.reqMarketDataType(LIVE_MARKET_DATA_TYPE)


@dataclass(frozen=True)
class _MarketDataProbe:
    ok: bool
    message: str
    ticked_type: str | None
    realtime: bool
    soft_failure: bool


def _probe_tick_evidence(market_data: object, data_type: int) -> bool:
    """Whether this ladder step produced usable evidence of market data flowing.

    A tick timestamp is always evidence. Frozen/delayed-frozen snapshots may populate prices
    without a fresh timestamp (they serve the last quote of a closed session), so on those steps
    a finite positive price field also counts -- probe-only, never used for execution pricing.
    """
    if isinstance(getattr(market_data, "time", None), datetime):
        return True
    if data_type not in FROZEN_MARKET_DATA_TYPE_CODES:
        return False
    return any(
        _valid_price(getattr(market_data, price_field, None)) is not None
        for price_field in ("bid", "ask", "last", "close")
    )


def _probe_is_market_open(settings: Settings) -> bool | None:
    """Best-effort market-hours lookup; a calendar failure must never fail a gateway check."""
    from poma.market_calendar import is_market_open

    try:
        return is_market_open(settings.market_calendar)
    except Exception:  # noqa: BLE001 - treat an unreadable calendar as "unknown", like closed
        return None


def _probe_market_data(ib: IB, settings: Settings) -> _MarketDataProbe:
    """Confirm the session actually receives a market data tick, not just an open socket.

    A Gateway session can be fully authenticated and trade-enabled while still returning no
    ticks at all -- e.g. a paper account whose market data sharing was never fully enabled, or
    an exchange data agreement that was never accepted for this account. Walking the full
    market data type ladder (live, frozen, delayed, delayed-frozen) makes the verdict conclusive
    even outside market hours: a frozen tick proves real-time entitlement off-hours, a
    delayed-only tick proves the real-time entitlement is missing.
    """
    contract = Stock(MARKET_DATA_PROBE_SYMBOL, "SMART", "USD")
    symbol = MARKET_DATA_PROBE_SYMBOL
    wait_seconds = settings.market_data_probe_wait_seconds
    ticked_code: int | None = None
    with _collect_market_data_errors(ib) as errors:
        for data_type in PROBE_MARKET_DATA_TYPE_LADDER:
            ib.reqMarketDataType(data_type)
            market_data = ib.reqMktData(contract, "", False, False)
            step_wait = wait_seconds * (
                DELAYED_PROBE_WAIT_MULTIPLIER if data_type in DELAYED_MARKET_DATA_TYPE_CODES else 1
            )
            ib.sleep(step_wait)
            ib.cancelMktData(getattr(market_data, "contract"))  # noqa: B009
            if _probe_tick_evidence(market_data, data_type):
                ticked_code = data_type
                break
        ib.reqMarketDataType(LIVE_MARKET_DATA_TYPE)

    if ticked_code is not None:
        type_name = MARKET_DATA_TYPE_NAMES[ticked_code]
        if ticked_code in REALTIME_MARKET_DATA_TYPE_CODES:
            suffix = "" if ticked_code == LIVE_MARKET_DATA_TYPE else "; market closed, last real-time quote"
            return _MarketDataProbe(
                ok=True,
                message=f"received {type_name} tick for {symbol} (real-time entitlement confirmed{suffix})",
                ticked_type=type_name,
                realtime=True,
                soft_failure=False,
            )
        detail = "; ".join(errors.per_symbol.get(symbol, []) or errors.general)
        reason = f"; ibkr said: {detail}" if detail else ""
        message = (
            f"received {type_name} tick for {symbol} but real-time entitlement MISSING "
            f"(best available: {type_name}){reason}"
        )
        if settings.require_live_execution_quotes:
            message += "; REQUIRE_LIVE_EXECUTION_QUOTES=true demands a live/frozen tick"
            return _MarketDataProbe(False, message, type_name, False, False)
        if not settings.allow_delayed_execution_quotes:
            message += "; ALLOW_DELAYED_EXECUTION_QUOTES=false so execution would block on every order"
            return _MarketDataProbe(False, message, type_name, False, False)
        return _MarketDataProbe(True, message, type_name, False, False)

    detail = "; ".join(errors.per_symbol.get(symbol, []) or errors.general)
    base = f"no market data tick received for {symbol} at any market data type (live/frozen/delayed/delayed_frozen)"
    if detail:
        return _MarketDataProbe(False, f"{base}; ibkr said: {detail}", None, False, False)
    market_open = _probe_is_market_open(settings)
    if market_open:
        return _MarketDataProbe(
            False,
            f"{base}; US market is open so silence is conclusive -- entitlement or market-data-farm failure",
            None,
            False,
            False,
        )
    message = f"market closed -- probe inconclusive ({base})"
    if settings.require_live_execution_quotes:
        return _MarketDataProbe(
            False,
            message + "; REQUIRE_LIVE_EXECUTION_QUOTES=true demands proof of a live/frozen tick",
            None,
            False,
            False,
        )
    return _MarketDataProbe(False, message, None, False, True)


def probe_ibkr(settings: Settings, *, timeout: float = 20.0) -> IbkrHealth:
    """Open a short-lived IBKR connection to confirm the API is reachable and authenticated.

    Places no orders. Used by the ``doctor`` command and ops verification.
    """
    ib = _connect_ib(settings, client_id=settings.ibkr_client_id + HEALTH_CLIENT_ID_OFFSET, timeout=timeout)
    try:
        accounts = [account for account in ib.managedAccounts() if account]
        server_time = str(ib.reqCurrentTime())
        stock_positions = sum(1 for item in ib.portfolio() if item.contract.secType == "STK")
        trading_permissions_ok, trading_permissions_message = _probe_trading_permissions(ib, settings)
        if settings.execution_price_source == ExecutionPriceSource.IBKR:
            probe = _probe_market_data(ib, settings)
        else:
            probe = _MarketDataProbe(
                ok=True,
                message=f"skipped (EXECUTION_PRICE_SOURCE={settings.execution_price_source.value})",
                ticked_type=None,
                realtime=False,
                soft_failure=False,
            )
        return IbkrHealth(
            connected=ib.isConnected(),
            accounts=accounts,
            server_time=server_time,
            stock_positions=stock_positions,
            trading_permissions_ok=trading_permissions_ok,
            trading_permissions_message=trading_permissions_message,
            market_data_ok=probe.ok,
            market_data_message=probe.message,
            market_data_type=probe.ticked_type,
            market_data_realtime=probe.realtime,
            market_data_soft_failure=probe.soft_failure,
        )
    finally:
        ib.disconnect()


class Broker(Protocol):
    def account_snapshot(self) -> AccountSnapshot:
        ...

    def submit_trades(
        self,
        trades: list[ProposedTrade],
        status_callback: OrderStatusCallback | None = None,
    ) -> list[OrderResult]:
        ...

    def fetch_open_order_snapshots(self) -> list[OpenOrderSnapshot]:
        """Poll the broker for currently open POMA orders, for lifecycle reconciliation."""
        ...

    def execution_quotes(self, tickers: list[str]) -> dict[str, ExecutionQuote]:
        """Fetch fresh execution-time quotes for the given tickers, keyed by ticker.

        Called immediately before order submission (and again during reconciliation replace
        decisions) so paper/live pricing anchors on a current broker quote instead of the
        Yahoo screener snapshot used for planning.
        """
        ...

    def cancel_order(self, order_id: int) -> bool:
        ...

    def replace_order(
        self,
        *,
        order_id: int,
        ticker: str,
        side: OrderSide,
        quantity: float,
        new_limit_price: float,
        order_ref: str,
    ) -> OpenOrderSnapshot:
        """Cancel the given order and place a fresh replacement, returning its new snapshot."""
        ...


class DryRunBroker:
    def __init__(self, fallback_portfolio_value_usd: float = 0.0) -> None:
        self.fallback_portfolio_value_usd = fallback_portfolio_value_usd

    def account_snapshot(self) -> AccountSnapshot:
        return AccountSnapshot(
            cash_usd=self.fallback_portfolio_value_usd,
            positions=(),
            positions_market_value_usd=0.0,
            net_liquidation_usd=self.fallback_portfolio_value_usd,
        )

    def submit_trades(
        self,
        trades: list[ProposedTrade],
        status_callback: OrderStatusCallback | None = None,
    ) -> list[OrderResult]:
        results = [
            OrderResult(
                ticker=trade.ticker,
                side=trade.side,
                quantity=trade.quantity,
                notional=trade.notional,
                order_id=None,
                status="dry_run",
                filled=0.0,
                average_fill_price=None,
                message="order not submitted in dry_run mode",
            )
            for trade in trades
        ]
        if status_callback is not None:
            for trade, result in zip(trades, results, strict=True):
                status_callback(trade, result)
        return results

    def fetch_open_order_snapshots(self) -> list[OpenOrderSnapshot]:
        return []

    def execution_quotes(self, tickers: list[str]) -> dict[str, ExecutionQuote]:
        # dry_run never places broker orders, so it has no execution-time quote source; the
        # engine keeps using the Yahoo snapshot reference price it already planned with.
        return {}

    def cancel_order(self, order_id: int) -> bool:
        return False

    def replace_order(
        self,
        *,
        order_id: int,
        ticker: str,
        side: OrderSide,
        quantity: float,
        new_limit_price: float,
        order_ref: str,
    ) -> OpenOrderSnapshot:
        raise NotImplementedError("dry_run mode never places broker orders to replace")


class IbkrBroker:
    def __init__(self, settings: Settings) -> None:
        settings.assert_safe_for_execution()
        self.settings = settings

    def _connect(self) -> IB:
        ib = _connect_ib(self.settings, client_id=self.settings.ibkr_client_id, timeout=IBKR_CONNECT_TIMEOUT_SECONDS)
        try:
            self._assert_connected(ib)
        except Exception:
            ib.disconnect()
            raise
        return ib

    def _assert_connected(self, ib: IB) -> None:
        if not ib.isConnected():
            raise BrokerUnavailable(
                f"IBKR API is not connected at {self.settings.ibkr_host}:{self.settings.ibkr_port}"
            )

    def _assert_ready_for_orders(self, ib: IB) -> None:
        """Validate the API handshake before creating any order objects.

        A listening socket is not enough; Gateway must be authenticated, expose the configured
        account, answer a lightweight server-time request, and be logged in with trading
        permissions. Failing here is safe because no order has been accepted by IBKR yet.
        """
        self._assert_connected(ib)
        accounts = [account for account in ib.managedAccounts() if account]
        if self.settings.ibkr_account and self.settings.ibkr_account not in accounts:
            raise BrokerUnavailable(
                f"configured IBKR_ACCOUNT={self.settings.ibkr_account} not in {accounts}"
            )
        ib.reqCurrentTime()
        trading_ok, trading_message = _probe_trading_permissions(ib, self.settings)
        if not trading_ok:
            raise BrokerUnavailable(trading_message)
        self._assert_connected(ib)

    def account_snapshot(self) -> AccountSnapshot:
        """Read cash, positions, and net liquidation from one IBKR session.

        Fetching both in the same connect/disconnect avoids two separate IBKR sessions racing
        against each other and reporting inconsistent cash vs. positions for the same rebalance.
        """
        ib = self._connect()
        try:
            positions = self._positions_from_ib(ib)
            positions_market_value = sum(position.market_value for position in positions)
            account_rows = list(_request_account_values(ib, self.settings.ibkr_account))
            cash_usd = _find_account_value(account_rows, USD_CASH_TAGS, self.settings.ibkr_account)
            net_liquidation_usd = _find_account_value(
                account_rows,
                NET_LIQUIDATION_TAGS,
                self.settings.ibkr_account,
            )
            summary_positions_value = _find_account_value(
                account_rows,
                GROSS_POSITION_VALUE_TAGS,
                self.settings.ibkr_account,
            )
            if summary_positions_value is not None and summary_positions_value > 0:
                positions_market_value = summary_positions_value
            if cash_usd is None:
                raise BrokerUnavailable("IBKR did not return a USD cash balance for the configured account")
            return AccountSnapshot(
                cash_usd=cash_usd,
                positions=tuple(positions),
                positions_market_value_usd=positions_market_value,
                net_liquidation_usd=net_liquidation_usd,
                account_id=self.settings.ibkr_account,
                timestamp_utc=datetime.now(UTC).isoformat(),
            )
        finally:
            ib.disconnect()

    def _positions_from_ib(self, ib: IB) -> list[CurrentPosition]:
        rows: list[CurrentPosition] = []
        for item in ib.portfolio():
            if item.contract.secType != "STK":
                continue
            account_mismatch = self.settings.ibkr_account and item.account != self.settings.ibkr_account
            if account_mismatch:
                continue
            rows.append(
                CurrentPosition(
                    ticker=item.contract.symbol.upper(),
                    quantity=float(item.position),
                    market_value=float(item.marketValue),
                )
            )
        return rows

    def submit_trades(
        self,
        trades: list[ProposedTrade],
        status_callback: OrderStatusCallback | None = None,
    ) -> list[OrderResult]:
        if not trades:
            return []
        ib: IB | None = None
        try:
            ib = self._connect()
            self._assert_ready_for_orders(ib)
        except Exception as exc:  # noqa: BLE001 - normalize connection/auth failures in reports
            if ib is not None:
                ib.disconnect()
            return self._unsubmitted_results(
                trades,
                status_callback,
                f"broker unavailable before submitting orders; no orders submitted: {exc}",
            )

        results: list[OrderResult] = []
        consecutive_acceptance_failures = 0
        try:
            for index, proposed in enumerate(trades):
                try:
                    self._assert_connected(ib)
                    contract = Stock(proposed.ticker, "SMART", "USD")
                    order = self._build_order(proposed)
                    if self.settings.ibkr_account:
                        order.account = self.settings.ibkr_account
                    submitted = ib.placeOrder(contract, order)
                    self._emit_status(
                        status_callback,
                        proposed,
                        self._order_result(
                            proposed,
                            submitted,
                            fallback_status="Submitted",
                            message="order submitted",
                        ),
                    )
                    result = self._wait_for_acceptance_or_terminal_status(
                        ib,
                        submitted,
                        proposed,
                        status_callback=status_callback,
                    )
                    results.append(result)

                    if result.status in ACCEPTED_STATUSES or result.filled > 0:
                        consecutive_acceptance_failures = 0
                        continue

                    consecutive_acceptance_failures += 1
                    if consecutive_acceptance_failures >= self.settings.max_consecutive_order_acceptance_failures:
                        remaining = trades[index + 1 :]
                        if remaining:
                            results.extend(
                                self._unsubmitted_results(
                                    remaining,
                                    status_callback,
                                    "stopped after "
                                    f"{consecutive_acceptance_failures} consecutive orders were "
                                    "not accepted by IBKR; no further orders submitted",
                                )
                            )
                        break
                except Exception as exc:  # noqa: BLE001 - report per-order failures without hiding context
                    if _looks_like_connection_failure(ib, exc):
                        results.extend(
                            self._unsubmitted_results(
                                trades[index:],
                                status_callback,
                                f"broker connection lost before order acceptance; no further "
                                f"orders submitted: {exc}",
                            )
                        )
                        break
                    result = _manual_result(proposed, "Failed", f"order not accepted by broker: {exc}")
                    self._emit_status(status_callback, proposed, result)
                    results.append(result)
        finally:
            ib.disconnect()
        return results

    def _unsubmitted_results(
        self,
        trades: Sequence[ProposedTrade],
        status_callback: OrderStatusCallback | None,
        message: str,
    ) -> list[OrderResult]:
        results = [_manual_result(trade, BROKER_UNAVAILABLE_STATUS, message) for trade in trades]
        if status_callback is not None:
            for trade, result in zip(trades, results, strict=True):
                status_callback(trade, result)
        return results

    @staticmethod
    def _emit_status(
        callback: OrderStatusCallback | None,
        proposed: ProposedTrade,
        result: OrderResult,
    ) -> None:
        if callback is not None:
            callback(proposed, result)

    def _wait_for_acceptance_or_terminal_status(
        self,
        ib: IB,
        trade: Trade,
        proposed: ProposedTrade,
        *,
        status_callback: OrderStatusCallback | None = None,
    ) -> OrderResult:
        """Wait only until IBKR accepts the order or proves it was not accepted.

        A working ``Submitted``/``PreSubmitted`` limit order is already accepted by IBKR. The
        rebalance run should not cancel it merely because it has not filled within the process
        timeout; subsequent broker/order-status telemetry can report final fills or cancellations.
        """
        deadline = time.monotonic() + self.settings.order_status_timeout_seconds
        last_status = trade.orderStatus.status or "Submitted"
        while time.monotonic() < deadline:
            status = trade.orderStatus.status or last_status
            if status != last_status:
                last_status = status
                self._emit_status(
                    status_callback,
                    proposed,
                    self._order_result(proposed, trade, fallback_status=status),
                )
            if status in ACCEPTED_STATUSES:
                return self._order_result(proposed, trade, fallback_status=status)
            if trade.isDone() or status in DONE_STATUSES:
                message = None
                if status != "Filled" and trade.orderStatus.filled == 0:
                    message = _trade_log_message(trade) or "order reached terminal status before acceptance"
                return self._order_result(proposed, trade, fallback_status=status, message=message)
            ib.sleep(1.0)

        timeout_message = "order did not reach broker accepted/working status before timeout"
        if self.settings.cancel_stale_orders:
            ib.cancelOrder(trade.order)
            cancel_message = f"{timeout_message}; cancel requested"
            self._emit_status(
                status_callback,
                proposed,
                self._order_result(
                    proposed,
                    trade,
                    fallback_status="PendingCancel",
                    message=cancel_message,
                ),
            )
            last_status = "PendingCancel"
            cancel_deadline = time.monotonic() + min(10, self.settings.order_status_timeout_seconds)
            while time.monotonic() < cancel_deadline:
                status = trade.orderStatus.status or last_status
                if status != last_status:
                    last_status = status
                    self._emit_status(
                        status_callback,
                        proposed,
                        self._order_result(proposed, trade, fallback_status=status),
                    )
                if trade.isDone() or status in DONE_STATUSES:
                    return self._order_result(
                        proposed,
                        trade,
                        fallback_status=status,
                        message=cancel_message,
                    )
                ib.sleep(1.0)
        return self._order_result(
            proposed,
            trade,
            fallback_status=ORDER_NOT_ACCEPTED_STATUS,
            message=timeout_message,
        )

    def _order_result(
        self,
        proposed: ProposedTrade,
        trade: Trade,
        *,
        fallback_status: str = "submitted",
        message: str | None = None,
    ) -> OrderResult:
        status = trade.orderStatus
        final_status = status.status or fallback_status
        diagnostic = message
        if diagnostic is None and final_status not in ACCEPTED_STATUSES:
            diagnostic = _trade_log_message(trade)
        return OrderResult(
            ticker=proposed.ticker,
            side=proposed.side,
            quantity=proposed.quantity,
            notional=proposed.notional,
            order_id=getattr(trade.order, "orderId", None),
            status=final_status,
            filled=float(status.filled or 0.0),
            average_fill_price=_none_if_zero(status.avgFillPrice),
            message=diagnostic,
            order_ref=proposed.order_ref,
            perm_id=getattr(trade.order, "permId", None),
        )

    def _build_order(self, trade: ProposedTrade) -> MarketOrder | LimitOrder:
        if self.settings.order_type == OrderType.MARKET:
            order: MarketOrder | LimitOrder = MarketOrder(trade.side.value, abs(trade.quantity))
        else:
            if trade.limit_price is None:
                raise ValueError(f"missing limit price for {trade.ticker}")
            order = LimitOrder(trade.side.value, abs(trade.quantity), trade.limit_price)
        order.tif = self.settings.order_time_in_force
        if trade.order_ref:
            order.orderRef = trade.order_ref
        return order

    def fetch_open_order_snapshots(self) -> list[OpenOrderSnapshot]:
        """Poll for currently open orders placed with a POMA orderRef, for reconciliation.

        ``reqAllOpenOrders`` (rather than ``reqOpenOrders``) is required here because the
        reconciliation command connects with its own client id, distinct from the client id
        that originally submitted the orders; only ``reqAllOpenOrders`` returns orders placed
        by other client ids on the same account.
        """
        ib = self._connect()
        try:
            self._assert_connected(ib)
            ib.reqAllOpenOrders()
            ib.sleep(1.0)
            snapshots: list[OpenOrderSnapshot] = []
            for trade in ib.openTrades():
                order = trade.order
                order_ref = str(getattr(order, "orderRef", "") or "")
                if not order_ref.startswith(f"{ORDER_REF_PREFIX}:"):
                    continue
                account = getattr(order, "account", "") or ""
                if self.settings.ibkr_account and account not in ("", self.settings.ibkr_account):
                    continue
                status = trade.orderStatus
                snapshots.append(
                    OpenOrderSnapshot(
                        order_ref=order_ref,
                        order_id=getattr(order, "orderId", None),
                        perm_id=getattr(order, "permId", None),
                        ticker=trade.contract.symbol.upper(),
                        side=OrderSide.BUY if order.action == "BUY" else OrderSide.SELL,
                        raw_status=status.status or "",
                        filled=float(status.filled or 0.0),
                        remaining=float(status.remaining or 0.0),
                        avg_fill_price=_none_if_zero(status.avgFillPrice),
                    )
                )
            return snapshots
        finally:
            ib.disconnect()

    def execution_quotes(self, tickers: list[str]) -> dict[str, ExecutionQuote]:
        """Snapshot bid/ask/last for every ticker in one IBKR session, right before submission.

        Every subscription is requested up front and cancelled together after a single wait,
        rather than one connect per ticker, so freshness is comparable across the whole batch.
        """
        unique_tickers = sorted({ticker.upper() for ticker in tickers})
        if not unique_tickers:
            return {}
        ib = self._connect()
        try:
            self._assert_connected(ib)
            with _collect_market_data_errors(ib) as errors:
                market_data_by_ticker: dict[str, object] = {
                    ticker: ib.reqMktData(Stock(ticker, "SMART", "USD"), "", False, False)
                    for ticker in unique_tickers
                }
                ib.sleep(EXECUTION_QUOTE_WAIT_SECONDS)
                _retry_missing_quotes_as_delayed(
                    ib, market_data_by_ticker, allow_delayed=self.settings.allow_delayed_execution_quotes
                )
                retrieved_at = datetime.now(UTC)
                quotes = {
                    ticker: _execution_quote_from_market_data(
                        ticker, market_data, retrieved_at, errors.per_symbol.get(ticker), errors.general
                    )
                    for ticker, market_data in market_data_by_ticker.items()
                }
            for market_data in market_data_by_ticker.values():
                ib.cancelMktData(getattr(market_data, "contract"))  # noqa: B009
            return quotes
        finally:
            ib.disconnect()

    def cancel_order(self, order_id: int) -> bool:
        ib = self._connect()
        try:
            self._assert_connected(ib)
            ib.reqAllOpenOrders()
            ib.sleep(1.0)
            target = next((trade.order for trade in ib.openTrades() if trade.order.orderId == order_id), None)
            if target is None:
                return False
            ib.cancelOrder(target)
            ib.sleep(1.0)
            return True
        finally:
            ib.disconnect()

    def replace_order(
        self,
        *,
        order_id: int,
        ticker: str,
        side: OrderSide,
        quantity: float,
        new_limit_price: float,
        order_ref: str,
    ) -> OpenOrderSnapshot:
        """Cancel the working order and place a fresh one with an updated limit price.

        This is a cancel-and-resubmit, not an in-place IBKR order modification, so the new
        order gets its own ``order_ref``/``orderId``; the caller is responsible for updating
        the ledger entry's identity to track the replacement.
        """
        ib = self._connect()
        try:
            self._assert_connected(ib)
            ib.reqAllOpenOrders()
            ib.sleep(1.0)
            target = next((trade.order for trade in ib.openTrades() if trade.order.orderId == order_id), None)
            if target is not None:
                ib.cancelOrder(target)
                ib.sleep(1.0)
            contract = Stock(ticker, "SMART", "USD")
            order = LimitOrder(side.value, abs(quantity), new_limit_price)
            order.tif = self.settings.order_time_in_force
            order.orderRef = order_ref
            if self.settings.ibkr_account:
                order.account = self.settings.ibkr_account
            trade = ib.placeOrder(contract, order)
            ib.sleep(1.0)
            status = trade.orderStatus
            return OpenOrderSnapshot(
                order_ref=order_ref,
                order_id=getattr(trade.order, "orderId", None),
                perm_id=getattr(trade.order, "permId", None),
                ticker=ticker,
                side=side,
                raw_status=status.status or "Submitted",
                filled=float(status.filled or 0.0),
                remaining=float(status.remaining or abs(quantity)),
                avg_fill_price=_none_if_zero(status.avgFillPrice),
            )
        finally:
            ib.disconnect()


def _request_account_values(ib: IB, account: str | None) -> Iterable[object]:
    """Return account balance rows from every IBKR cache exposed by the auth session.

    IB Gateway/ib_insync can expose balances through account summary rows, account value
    rows populated by the authenticated session, or both. Querying both makes rebalances
    resilient to sessions where one cache is empty even though the authenticated account
    data is available.
    """
    rows: list[object] = []
    for summary_account in _account_summary_queries(account):
        try:
            rows.extend(ib.accountSummary(summary_account))
        except TypeError:
            rows.extend(ib.accountSummary())
            break
    for values_account in _account_values_queries(account):
        account_values = getattr(ib, "accountValues", None)
        if account_values is None:
            break
        try:
            rows.extend(account_values(values_account))
        except TypeError:
            rows.extend(account_values())
            break
    return rows


def _account_summary_queries(account: str | None) -> tuple[str, ...]:
    if account:
        return (account, "")
    return ("",)


def _account_values_queries(account: str | None) -> tuple[str, ...]:
    if account:
        return (account, "")
    return ("",)


def _find_account_value(rows: Iterable[object], tags: tuple[str, ...], account: str | None) -> float | None:
    for row in rows:
        if account and getattr(row, "account", "") not in ("", account):
            continue
        if getattr(row, "tag", "") not in tags:
            continue
        currency = str(getattr(row, "currency", "") or "USD").upper()
        if currency not in {"USD", "BASE"}:
            continue
        parsed = _parse_account_value(getattr(row, "value", None))
        if parsed is not None:
            return parsed
    return None


def _parse_account_value(value: object) -> float | None:
    try:
        parsed = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _probe_trading_permissions(ib: IB, settings: Settings) -> tuple[bool, str]:
    """Verify the current session can validate orders without transmitting a live order."""
    contract = Stock(TRADING_PERMISSION_PROBE_SYMBOL, "SMART", "USD")
    order = LimitOrder("BUY", 1, TRADING_PERMISSION_PROBE_LIMIT_PRICE)
    order.whatIf = True
    # IBKR rejects what-if orders with transmit=False (Error 321 "What-If order should have
    # transmit flag set to TRUE"); whatIf=True already guarantees nothing is ever executed.
    order.transmit = True
    if settings.ibkr_account:
        order.account = settings.ibkr_account
    try:
        state = ib.whatIfOrder(contract, order)
    except Exception as exc:  # noqa: BLE001 - return the broker's readiness reason to ops/doctor
        detail = str(exc) or exc.__class__.__name__
        return (
            False,
            "IBKR session is connected but not trading-enabled. "
            "Gateway may be logged in without Trading/Market Data permissions or another "
            f"primary trading session is active: {detail}",
        )
    warning = str(getattr(state, "warningText", "") or "").strip()
    init_margin = str(getattr(state, "initMarginChange", "") or "").strip()
    detail_parts = [
        f"what-if order preview accepted for {TRADING_PERMISSION_PROBE_SYMBOL}",
    ]
    if init_margin:
        detail_parts.append(f"init_margin_change={init_margin}")
    if warning:
        detail_parts.append(f"warning={warning}")
    return True, ", ".join(detail_parts)


def _looks_like_connection_failure(ib: IB, exc: Exception) -> bool:
    if isinstance(exc, BrokerUnavailable):
        return True
    try:
        if not ib.isConnected():
            return True
    except Exception:  # noqa: BLE001 - an unreadable connection state is also unsafe
        return True
    message = str(exc).lower()
    return "not connected" in message or "connection" in message


def _trade_log_message(trade: Trade) -> str | None:
    messages: list[str] = []
    for entry in getattr(trade, "log", []) or []:
        message = str(getattr(entry, "message", "") or "").strip()
        if not message:
            continue
        status = str(getattr(entry, "status", "") or "").strip()
        error_code = getattr(entry, "errorCode", None)
        prefix = ""
        if status:
            prefix += f"{status}: "
        if error_code not in (None, 0, "0"):
            prefix += f"{error_code}: "
        rendered = f"{prefix}{message}"
        if rendered not in messages:
            messages.append(rendered)
    if not messages:
        return None
    return "; ".join(messages[-3:])


def _manual_result(proposed: ProposedTrade, status: str, message: str | None = None) -> OrderResult:
    return OrderResult(
        ticker=proposed.ticker,
        side=proposed.side,
        quantity=proposed.quantity,
        notional=proposed.notional,
        order_id=None,
        status=status,
        filled=0.0,
        average_fill_price=None,
        message=message,
        order_ref=proposed.order_ref,
    )


def _valid_price(value: float | None) -> float | None:
    """Normalize an IBKR tick field: NaN or non-positive values mean "no data"."""
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or parsed <= 0:
        return None
    return parsed


def _execution_quote_from_market_data(
    ticker: str,
    market_data: object,
    retrieved_at: datetime,
    per_symbol_errors: list[str] | None,
    general_errors: list[str],
) -> ExecutionQuote:
    bid = _valid_price(getattr(market_data, "bid", None))
    ask = _valid_price(getattr(market_data, "ask", None))
    last = _valid_price(getattr(market_data, "last", None))
    close = _valid_price(getattr(market_data, "close", None))
    tick_time = getattr(market_data, "time", None)
    tick_time_iso = tick_time.isoformat() if isinstance(tick_time, datetime) else None
    age_seconds = (retrieved_at - tick_time).total_seconds() if isinstance(tick_time, datetime) else None
    market_data_type = MARKET_DATA_TYPE_NAMES.get(getattr(market_data, "marketDataType", None))
    broker_error = None
    if age_seconds is None:
        # A farm-wide connectivity/entitlement problem fires without a per-symbol contract, so
        # every ticker in the batch falls back to the same general reason rather than reporting
        # nothing just because IBKR didn't attribute the error to this specific symbol.
        combined = per_symbol_errors or general_errors
        broker_error = "; ".join(combined) if combined else None
    return ExecutionQuote(
        ticker=ticker,
        source="ibkr",
        retrieved_at_utc=retrieved_at.isoformat(),
        selected_price_as_of_utc=tick_time_iso,
        age_seconds=age_seconds,
        bid=bid,
        ask=ask,
        last=last,
        close=close,
        spread_bps=compute_spread_bps(bid, ask),
        is_delayed=market_data_type in DELAYED_MARKET_DATA_TYPES,
        raw_market_data_type=market_data_type,
        broker_error=broker_error,
    )


def _none_if_zero(value: float | None) -> float | None:
    if value is None or value == 0:
        return None
    return float(value)


def _is_idempotent_replay(result: OrderResult) -> bool:
    """An IdempotentReplay result reports on an order an earlier attempt already submitted.

    Its ``message`` is purely informational (not a diagnostic of a problem), and the order it
    reports on was, by construction, already accepted by (or resolved at) the broker before this
    run started. It must not be treated as a submission failure just because its own ``status``
    string is not one of the broker's own accepted-order statuses.
    """
    return result.status == IDEMPOTENT_REPLAY_STATUS


def order_results_have_issues(results: list[OrderResult]) -> bool:
    return any(
        not _is_idempotent_replay(result) and (result.status not in SUCCESS_STATUSES or result.message)
        for result in results
    )


def order_results_have_no_accepted_orders(results: list[OrderResult]) -> bool:
    return bool(results) and all(
        not _is_idempotent_replay(result) and result.filled <= 0 and result.status not in SUCCESS_STATUSES
        for result in results
    )


def build_broker(settings: Settings) -> Broker:
    if settings.trading_mode == TradingMode.DRY_RUN:
        return DryRunBroker(settings.dry_run_portfolio_value_usd)
    return IbkrBroker(settings)
