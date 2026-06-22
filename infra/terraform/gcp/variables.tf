variable "project_id" { type = string }
variable "region" { type = string default = "asia-southeast1" }
variable "environment" { type = string default = "production" }
variable "name_prefix" { type = string default = "poma" }
variable "artifact_registry_repository" { type = string default = "poma" }
variable "cloud_run_job_name" { type = string default = "poma-rebalance" }
variable "initial_image" { type = string description = "Bootstrap image URI; deploy workflow updates this after first build." }
variable "trading_mode" { type = string default = "dry_run" }
variable "rebalance_frequency" { type = string default = "monthly" }
variable "rank_lookback_periods" { type = number default = 21 }
variable "portfolio_value_usd" { type = number default = 10000 }
variable "cash_buffer_pct" { type = number default = 0.02 }
variable "max_position_pct" { type = number default = 0.10 }
variable "max_turnover_pct" { type = number default = 0.35 }
variable "min_trade_notional_usd" { type = number default = 25 }
variable "executor_endpoint" { type = string default = "" }
variable "cron_schedule" { type = string default = "0 6 1 * *" }
variable "scheduler_time_zone" { type = string default = "Asia/Singapore" }
