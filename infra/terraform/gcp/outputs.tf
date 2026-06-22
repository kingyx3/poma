output "artifact_registry_repository" { value = google_artifact_registry_repository.poma.repository_id }
output "cloud_run_job_name" { value = google_cloud_run_v2_job.rebalance.name }
output "runtime_service_account" { value = google_service_account.runtime.email }
output "fmp_api_key_secret" { value = google_secret_manager_secret.fmp_api_key.secret_id }
output "executor_api_key_secret" { value = google_secret_manager_secret.executor_api_key.secret_id }
output "scheduler_job_name" { value = google_cloud_scheduler_job.rebalance.name }
