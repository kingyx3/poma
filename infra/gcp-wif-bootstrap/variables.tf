variable "project_id" {
  description = "GCP project id that hosts the POMA deployer identity."
  type        = string
}

variable "github_repository" {
  description = "GitHub repository allowed to impersonate the deployer service account, in owner/name form."
  type        = string
  default     = "kingyx3/poma"

  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "github_repository must be in owner/name form."
  }
}

variable "pool_id" {
  description = "Workload Identity Pool id."
  type        = string
  default     = "poma-github"
}

variable "provider_id" {
  description = "Workload Identity Pool provider id for GitHub OIDC."
  type        = string
  default     = "github"
}

variable "service_account_id" {
  description = "Service account id for GitHub Actions deployment."
  type        = string
  default     = "poma-github-deployer"
}

variable "project_roles" {
  description = "Project-level roles granted to the deployer service account."
  type        = set(string)
  default = [
    "roles/compute.admin",
    "roles/iam.serviceAccountUser",
    "roles/iap.tunnelResourceAccessor",
    "roles/serviceusage.serviceUsageAdmin",
    "roles/storage.objectAdmin",
  ]
}
