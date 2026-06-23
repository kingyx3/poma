output "workload_identity_provider" {
  description = "Full Workload Identity Provider resource name for google-github-actions/auth."
  value       = "projects/${data.google_project.current.number}/locations/global/workloadIdentityPools/${google_iam_workload_identity_pool.github.workload_identity_pool_id}/providers/${google_iam_workload_identity_pool_provider.github.workload_identity_pool_provider_id}"
}

output "service_account_email" {
  description = "Service account email for google-github-actions/auth impersonation."
  value       = google_service_account.github_deployer.email
}

output "github_repository" {
  description = "GitHub repository trusted by this WIF provider."
  value       = var.github_repository
}
