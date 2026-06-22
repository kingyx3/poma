from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import Field, PositiveFloat, PositiveInt, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TradingMode(StrEnum):
    DRY_RUN = "dry_run"
    PAPER = "paper"
    LIVE = "live"


class RebalanceFrequency(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(default="development", alias="APP_ENV")
    trading_mode: TradingMode = Field(default=TradingMode.DRY_RUN, alias="TRADING_MODE")
    allow_live_trading: bool = Field(default=False, alias="ALLOW_LIVE_TRADING")

    data_provider: str = Field(default="fmp", alias="DATA_PROVIDER")
    fmp_api_key: str | None = Field(default=None, alias="FMP_API_KEY")
    fmp_base_url: str = Field(default="https://financialmodelingprep.com/stable", alias="FMP_BASE_URL")

    universe: str = Field(default="nasdaq100", alias="UNIVERSE")
    rank_lookback_periods: PositiveInt = Field(default=21, alias="RANK_LOOKBACK_PERIODS")
    rebalance_frequency: RebalanceFrequency = Field(
        default=RebalanceFrequency.MONTHLY, alias="REBALANCE_FREQUENCY"
    )

    portfolio_value_usd: PositiveFloat = Field(default=10_000.0, alias="PORTFOLIO_VALUE_USD")
    cash_buffer_pct: float = Field(default=0.02, alias="CASH_BUFFER_PCT")
    max_position_pct: float = Field(default=0.10, alias="MAX_POSITION_PCT")
    max_turnover_pct: float = Field(default=0.35, alias="MAX_TURNOVER_PCT")
    min_trade_notional_usd: PositiveFloat = Field(default=25.0, alias="MIN_TRADE_NOTIONAL_USD")

    ibkr_host: str = Field(default="127.0.0.1", alias="IBKR_HOST")
    ibkr_port: int = Field(default=7497, alias="IBKR_PORT")
    ibkr_client_id: int = Field(default=101, alias="IBKR_CLIENT_ID")
    ibkr_account: str | None = Field(default=None, alias="IBKR_ACCOUNT")

    executor_endpoint: str | None = Field(default=None, alias="EXECUTOR_ENDPOINT")
    executor_api_key: str | None = Field(default=None, alias="EXECUTOR_API_KEY")

    state_dir: Path = Field(default=Path("var"), alias="STATE_DIR")
    report_dir: Path = Field(default=Path("reports"), alias="REPORT_DIR")

    @field_validator("cash_buffer_pct", "max_position_pct", "max_turnover_pct")
    @classmethod
    def pct_between_zero_and_one(cls, value: float) -> float:
        if not 0 <= value <= 1:
            raise ValueError("percentage settings must be between 0 and 1")
        return value

    def assert_safe_for_execution(self) -> None:
        if self.trading_mode == TradingMode.LIVE and not self.allow_live_trading:
            raise RuntimeError("LIVE trading requires ALLOW_LIVE_TRADING=true")


def get_settings() -> Settings:
    return Settings()
