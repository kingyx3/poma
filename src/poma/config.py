from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import Field, PositiveFloat, PositiveInt, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from poma.execution_policy import build_execution_rules
from poma.models import InstrumentExecutionRule
from poma.portfolio import CASH_STRATEGY_NAME, DEFAULT_STRATEGY_ALLOCATIONS, parse_strategy_allocations
from poma.strategies import default_registry


class TradingMode(StrEnum):
    DRY_RUN = "dry_run"
    PAPER = "paper"
    LIVE = "live"


class OrderType(StrEnum):
    LIMIT = "limit"
    MARKET = "market"


class ManagedCapMode(StrEnum):
    """How paper/live rebalances turn broker equity into a portfolio value to size against."""

    BROKER_TOTAL = "broker_total"
    MIN_OF_BROKER_TOTAL_AND_CAP = "min_of_broker_total_and_cap"


class StaleOrderPolicy(StrEnum):
    """What to do when a new rebalance finds unresolved open orders from a prior session."""

    BLOCK = "block"
    CANCEL = "cancel"


class ExecutionPriceSource(StrEnum):
    """Where paper/live order pricing reads its execution-time reference quote from."""

    IBKR = "ibkr"
    SNAPSHOT = "snapshot"


class ExecutionPriceBasis(StrEnum):
    """Which part of the quote a trade's reference price is selected from."""

    SIDE_OF_MARKET = "side_of_market"
    MIDPOINT = "midpoint"
    LAST = "last"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = Field(default="development", alias="APP_ENV")
    trading_mode: TradingMode = Field(default=TradingMode.DRY_RUN, alias="TRADING_MODE")
    allow_live_trading: bool = Field(default=False, alias="ALLOW_LIVE_TRADING")

    market_calendar: str = Field(default="NASDAQ", alias="MARKET_CALENDAR")
    rebalance_after_open_minutes: PositiveInt = Field(
        default=10,
        alias="REBALANCE_AFTER_OPEN_MINUTES",
    )

    data_provider: str = Field(default="yahoo", alias="DATA_PROVIDER")
    yahoo_screener_limit: PositiveInt = Field(default=500, alias="YAHOO_SCREENER_LIMIT")
    yahoo_screener_page_size: PositiveInt = Field(default=250, alias="YAHOO_SCREENER_PAGE_SIZE")

    universe: str = Field(default="us_top_market_cap", alias="UNIVERSE")
    rank_lookback_days: PositiveInt = Field(default=90, alias="RANK_LOOKBACK_DAYS")
    max_holdings: PositiveInt = Field(default=50, alias="MAX_HOLDINGS")
    strategy_allocations: str = Field(
        default=DEFAULT_STRATEGY_ALLOCATIONS,
        alias="STRATEGY_ALLOCATIONS",
    )

    dry_run_portfolio_value_usd: PositiveFloat = Field(
        default=10_000.0,
        alias="DRY_RUN_PORTFOLIO_VALUE_USD",
    )
    managed_cap_usd: float = Field(default=0.0, alias="MANAGED_CAP_USD")
    managed_cap_mode: ManagedCapMode = Field(
        default=ManagedCapMode.BROKER_TOTAL,
        alias="MANAGED_CAP_MODE",
    )
    max_position_pct: float = Field(default=0.10, alias="MAX_POSITION_PCT")
    max_turnover_pct: float = Field(default=1.0, alias="MAX_TURNOVER_PCT")
    min_trade_notional_usd: PositiveFloat = Field(
        default=25.0,
        alias="MIN_TRADE_NOTIONAL_USD",
    )
    min_weight_delta_pct: float = Field(default=0.0025, alias="MIN_WEIGHT_DELTA_PCT")
    estimated_transaction_cost_bps: float = Field(default=0.0, alias="ESTIMATED_TRANSACTION_COST_BPS")
    estimated_transaction_cost_fixed_usd: float = Field(
        default=0.0,
        alias="ESTIMATED_TRANSACTION_COST_FIXED_USD",
    )

    order_type: OrderType = Field(default=OrderType.LIMIT, alias="ORDER_TYPE")
    allow_market_orders: bool = Field(default=False, alias="ALLOW_MARKET_ORDERS")
    limit_offset_bps: float = Field(default=10.0, alias="LIMIT_OFFSET_BPS")
    max_order_notional_usd: PositiveFloat = Field(
        default=2_000.0,
        alias="MAX_ORDER_NOTIONAL_USD",
    )
    max_daily_trades: PositiveInt = Field(default=100, alias="MAX_DAILY_TRADES")
    # IBKR rejects fractional-sized API orders (error 10243), so whole-share sizing is the
    # default. FRACTIONAL_SHARES=true restores fractional sizing for accounts confirmed to
    # accept fractional API orders, with NON_FRACTIONAL_TICKERS as per-ticker exceptions.
    fractional_shares: bool = Field(default=False, alias="FRACTIONAL_SHARES")
    non_fractional_tickers: str = Field(default="", alias="NON_FRACTIONAL_TICKERS")
    order_status_timeout_seconds: PositiveInt = Field(
        default=60,
        alias="ORDER_STATUS_TIMEOUT_SECONDS",
    )
    cancel_stale_orders: bool = Field(default=True, alias="CANCEL_STALE_ORDERS")
    max_consecutive_order_acceptance_failures: PositiveInt = Field(
        default=3,
        alias="MAX_CONSECUTIVE_ORDER_ACCEPTANCE_FAILURES",
    )
    order_time_in_force: str = Field(default="DAY", alias="ORDER_TIME_IN_FORCE")
    replace_after_seconds: PositiveInt = Field(default=120, alias="REPLACE_AFTER_SECONDS")
    cancel_after_seconds: PositiveInt = Field(default=300, alias="CANCEL_AFTER_SECONDS")
    replace_price_improvement_bps: float = Field(default=15.0, alias="REPLACE_PRICE_IMPROVEMENT_BPS")
    stale_order_policy: StaleOrderPolicy = Field(default=StaleOrderPolicy.BLOCK, alias="STALE_ORDER_POLICY")

    # Execution pricing: paper/live orders anchor on a fresh broker quote captured immediately
    # before submission, not the Yahoo screener snapshot used for universe/target planning.
    execution_price_source: ExecutionPriceSource = Field(
        default=ExecutionPriceSource.IBKR,
        alias="EXECUTION_PRICE_SOURCE",
    )
    execution_price_basis: ExecutionPriceBasis = Field(
        default=ExecutionPriceBasis.SIDE_OF_MARKET,
        alias="EXECUTION_PRICE_BASIS",
    )
    execution_quote_max_age_seconds: PositiveInt = Field(
        default=60,
        alias="EXECUTION_QUOTE_MAX_AGE_SECONDS",
    )
    execution_max_spread_bps: PositiveFloat = Field(default=50.0, alias="EXECUTION_MAX_SPREAD_BPS")
    # Whether a delayed broker quote may price an order. Defaults by trading mode when unset:
    # true for dry_run/paper (accounts commonly lack the separate IBKR "API market data"
    # real-time opt-in even when delayed data is available), false for live (deploy validation
    # additionally hard-fails live with this set to true).
    allow_delayed_execution_quotes: bool | None = Field(
        default=None,
        alias="ALLOW_DELAYED_EXECUTION_QUOTES",
    )
    # When true, `poma ibkr-check` hard-fails unless the market data probe receives a
    # real-time-class tick (live or frozen); delayed-only or silent probes are never soft-passed.
    # Turn on to prove an account's real-time API market data entitlement actually works.
    require_live_execution_quotes: bool = Field(default=False, alias="REQUIRE_LIVE_EXECUTION_QUOTES")
    market_data_probe_wait_seconds: PositiveFloat = Field(
        default=5.0,
        alias="MARKET_DATA_PROBE_WAIT_SECONDS",
    )
    allow_last_price_fallback: bool = Field(default=False, alias="ALLOW_LAST_PRICE_FALLBACK")
    allow_unsafe_execution_price_source: bool = Field(
        default=False,
        alias="ALLOW_UNSAFE_EXECUTION_PRICE_SOURCE",
    )

    ibkr_host: str = Field(default="127.0.0.1", alias="IBKR_HOST")
    ibkr_port: int = Field(default=7497, alias="IBKR_PORT")
    ibkr_client_id: int = Field(default=101, alias="IBKR_CLIENT_ID")
    ibkr_account: str | None = Field(default=None, alias="IBKR_ACCOUNT")

    state_dir: Path = Field(default=Path("state"), alias="STATE_DIR")
    data_dir: Path = Field(default=Path("data"), alias="DATA_DIR")
    report_dir: Path = Field(default=Path("reports"), alias="REPORT_DIR")

    telegram_bot_token: str | None = Field(default=None, alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str | None = Field(default=None, alias="TELEGRAM_CHAT_ID")

    @field_validator(
        "max_position_pct",
        "max_turnover_pct",
        "min_weight_delta_pct",
    )
    @classmethod
    def pct_between_zero_and_one(cls, value: float) -> float:
        if not 0 <= value <= 1:
            raise ValueError("percentage settings must be between 0 and 1")
        return value

    @field_validator(
        "limit_offset_bps",
        "replace_price_improvement_bps",
        "estimated_transaction_cost_bps",
    )
    @classmethod
    def bps_non_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("basis-point settings must be non-negative")
        return value

    @field_validator("managed_cap_usd", "estimated_transaction_cost_fixed_usd")
    @classmethod
    def usd_amount_non_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("USD amount settings must be non-negative")
        return value

    @model_validator(mode="after")
    def validate_runtime_config(self) -> Settings:
        if not self.telegram_bot_token or not self.telegram_chat_id:
            raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")

        if self.allow_delayed_execution_quotes is None:
            self.allow_delayed_execution_quotes = self.trading_mode != TradingMode.LIVE

        allocations = parse_strategy_allocations(self.strategy_allocations)
        registry = default_registry()
        unknown = sorted(
            name
            for name in allocations
            if name != CASH_STRATEGY_NAME and name not in registry.names()
        )
        if unknown:
            available = ", ".join(registry.names()) or "none"
            raise ValueError(
                f"STRATEGY_ALLOCATIONS references unregistered strategies {unknown}; "
                f"available strategies: {available}"
            )
        if (
            self.managed_cap_mode == ManagedCapMode.MIN_OF_BROKER_TOTAL_AND_CAP
            and self.managed_cap_usd <= 0
        ):
            raise ValueError(
                "MANAGED_CAP_USD must be greater than 0 when "
                "MANAGED_CAP_MODE=min_of_broker_total_and_cap"
            )
        if self.cancel_after_seconds <= self.replace_after_seconds:
            raise ValueError("CANCEL_AFTER_SECONDS must be greater than REPLACE_AFTER_SECONDS")
        if (
            self.execution_price_basis == ExecutionPriceBasis.LAST
            and not self.allow_last_price_fallback
        ):
            raise ValueError(
                "EXECUTION_PRICE_BASIS=last requires ALLOW_LAST_PRICE_FALLBACK=true"
            )
        live_unsafe_price_source = (
            self.trading_mode == TradingMode.LIVE
            and self.execution_price_source == ExecutionPriceSource.SNAPSHOT
            and not self.allow_unsafe_execution_price_source
        )
        if live_unsafe_price_source:
            raise ValueError(
                "LIVE trading requires EXECUTION_PRICE_SOURCE=ibkr unless "
                "ALLOW_UNSAFE_EXECUTION_PRICE_SOURCE=true"
            )
        return self

    def strategy_allocation_map(self) -> dict[str, float]:
        return parse_strategy_allocations(self.strategy_allocations)

    def execution_rules(self) -> dict[str, InstrumentExecutionRule]:
        return build_execution_rules(
            self.non_fractional_tickers,
            fractional_shares=self.fractional_shares,
        )

    def assert_safe_for_execution(self) -> None:
        if self.trading_mode in {TradingMode.PAPER, TradingMode.LIVE} and not self.ibkr_account:
            raise RuntimeError(f"{self.trading_mode.value} trading requires IBKR_ACCOUNT")
        if self.trading_mode == TradingMode.LIVE and not self.allow_live_trading:
            raise RuntimeError("LIVE trading requires ALLOW_LIVE_TRADING=true")
        market_order_blocked = (
            self.trading_mode == TradingMode.LIVE
            and self.order_type == OrderType.MARKET
            and not self.allow_market_orders
        )
        if market_order_blocked:
            raise RuntimeError("LIVE market orders require ALLOW_MARKET_ORDERS=true")


def get_settings() -> Settings:
    return Settings()
