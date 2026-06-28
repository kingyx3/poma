from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import Field, PositiveFloat, PositiveInt, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TradingMode(StrEnum):
    DRY_RUN = "dry_run"
    PAPER = "paper"
    LIVE = "live"


class OrderType(StrEnum):
    LIMIT = "limit"
    MARKET = "market"


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
    max_holdings: PositiveInt = Field(default=100, alias="MAX_HOLDINGS")

    portfolio_value_usd: PositiveFloat = Field(default=10_000.0, alias="PORTFOLIO_VALUE_USD")
    cash_buffer_pct: float = Field(default=0.02, alias="CASH_BUFFER_PCT")
    max_position_pct: float = Field(default=0.10, alias="MAX_POSITION_PCT")
    max_turnover_pct: float = Field(default=1.0, alias="MAX_TURNOVER_PCT")
    min_trade_notional_usd: PositiveFloat = Field(
        default=25.0,
        alias="MIN_TRADE_NOTIONAL_USD",
    )
    min_weight_delta_pct: float = Field(default=0.0025, alias="MIN_WEIGHT_DELTA_PCT")

    order_type: OrderType = Field(default=OrderType.LIMIT, alias="ORDER_TYPE")
    allow_market_orders: bool = Field(default=False, alias="ALLOW_MARKET_ORDERS")
    limit_offset_bps: float = Field(default=10.0, alias="LIMIT_OFFSET_BPS")
    max_order_notional_usd: PositiveFloat = Field(
        default=2_000.0,
        alias="MAX_ORDER_NOTIONAL_USD",
    )
    max_daily_trades: PositiveInt = Field(default=100, alias="MAX_DAILY_TRADES")
    order_status_timeout_seconds: PositiveInt = Field(
        default=60,
        alias="ORDER_STATUS_TIMEOUT_SECONDS",
    )
    cancel_stale_orders: bool = Field(default=True, alias="CANCEL_STALE_ORDERS")

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
        "cash_buffer_pct",
        "max_position_pct",
        "max_turnover_pct",
        "min_weight_delta_pct",
    )
    @classmethod
    def pct_between_zero_and_one(cls, value: float) -> float:
        if not 0 <= value <= 1:
            raise ValueError("percentage settings must be between 0 and 1")
        return value

    @field_validator("limit_offset_bps")
    @classmethod
    def bps_non_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("basis-point settings must be non-negative")
        return value

    @model_validator(mode="after")
    def telegram_is_required(self) -> Settings:
        if not self.telegram_bot_token or not self.telegram_chat_id:
            raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
        return self

    def assert_safe_for_execution(self) -> None:
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
