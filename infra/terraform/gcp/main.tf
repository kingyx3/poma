terraform {
  required_version = ">= 1.6.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.40"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

resource "google_artifact_registry_repository" "poma" {
  location      = var.region
  repository_id = var.artifact_registry_repository
  description   = "POMA container images"
  format        = "DOCKER"
}

resource "google_service_account" "runtime" {
  account_id   = "${var.name_prefix}-runtime"
  display_name = "POMA Cloud Run runtime"
}

resource "google_secret_manager_secret" "fmp_api_key" {
  secret_id = "${var.name_prefix}-fmp-api-key"
  replication { auto {} }
}

resource "google_secret_manager_secret" "executor_api_key" {
  secret_id = "${var.name_prefix}-executor-api-key"
  replication { auto {} }
}

resource "google_secret_manager_secret_iam_member" "runtime_fmp" {
  secret_id = google_secret_manager_secret.fmp_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_secret_manager_secret_iam_member" "runtime_executor" {
  secret_id = google_secret_manager_secret.executor_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_cloud_run_v2_job" "rebalance" {
  name     = var.cloud_run_job_name
  location = var.region

  template {
    template {
      service_account = google_service_account.runtime.email
      max_retries     = 0
      timeout         = "900s"

      containers {
        image = var.initial_image
        args  = ["rebalance"]

        env { name = "APP_ENV" value = var.environment }
        env { name = "TRADING_MODE" value = var.trading_mode }
        env { name = "DATA_PROVIDER" value = "fmp" }
        env { name = "UNIVERSE" value = "nasdaq100" }
        env { name = "REBALANCE_FREQUENCY" value = var.rebalance_frequency }
        env { name = "RANK_LOOKBACK_PERIODS" value = tostring(var.rank_lookback_periods) }
        env { name = "PORTFOLIO_VALUE_USD" value = tostring(var.portfolio_value_usd) }
        env { name = "CASH_BUFFER_PCT" value = tostring(var.cash_buffer_pct) }
        env { name = "MAX_POSITION_PCT" value = tostring(var.max_position_pct) }
        env { name = "MAX_TURNOVER_PCT" value = tostring(var.max_turnover_pct) }
        env { name = "MIN_TRADE_NOTIONAL_USD" value = tostring(var.min_trade_notional_usd) }
        env { name = "EXECUTOR_ENDPOINT" value = var.executor_endpoint }
        env {
          name = "FMP_API_KEY"
          value_source { secret_key_ref { secret = google_secret_manager_secret.fmp_api_key.secret_id version = "latest" } }
        }
        env {
          name = "EXECUTOR_API_KEY"
          value_source { secret_key_ref { secret = google_secret_manager_secret.executor_api_key.secret_id version = "latest" } }
        }
      }
    }
  }
}

resource "google_cloud_scheduler_job" "rebalance" {
  name        = "${var.name_prefix}-rebalance"
  description = "Scheduled POMA Nasdaq-100 rebalance"
  schedule    = var.cron_schedule
  time_zone   = var.scheduler_time_zone

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.rebalance.name}:run"

    oauth_token {
      service_account_email = google_service_account.runtime.email
    }
  }
}

resource "google_project_iam_member" "runtime_run_invoker" {
  project = var.project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}
