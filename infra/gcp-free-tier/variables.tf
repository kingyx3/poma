variable "project_id" {
  description = "GCP project id."
  type        = string
}

variable "region" {
  description = "Free-tier eligible Compute Engine region. Keep this in a US free-tier region."
  type        = string
  default     = "us-west1"

  validation {
    condition     = contains(["us-west1", "us-central1", "us-east1"], var.region)
    error_message = "Use one of the Compute Engine free-tier eligible regions: us-west1, us-central1, us-east1."
  }
}

variable "zone" {
  description = "Zone within the selected region."
  type        = string
  default     = "us-west1-b"

  validation {
    condition     = startswith(var.zone, "${var.region}-")
    error_message = "zone must be inside region."
  }
}

variable "instance_name" {
  description = "Name of the single POMA VM."
  type        = string
  default     = "poma-free-tier"

  validation {
    condition     = can(regex("^[a-z]([-a-z0-9]*[a-z0-9])?$", var.instance_name))
    error_message = "instance_name must be a valid Compute Engine instance name."
  }
}

variable "boot_disk_size_gb" {
  description = "Standard persistent disk size. Keep <= 30 GB to stay inside the Compute Engine Free Tier disk allowance."
  type        = number
  default     = 30

  validation {
    condition     = var.boot_disk_size_gb > 0 && var.boot_disk_size_gb <= 30
    error_message = "boot_disk_size_gb must be between 1 and 30."
  }
}

variable "network_cidr" {
  description = "CIDR range for the dedicated POMA subnet."
  type        = string
  default     = "10.50.0.0/24"
}

variable "budget_billing_account" {
  description = "Optional Cloud Billing account resource name for a monthly budget alert. Leave empty to skip budget creation."
  type        = string
  default     = ""
}

variable "monthly_budget_usd" {
  description = "Whole-dollar monthly budget threshold when budget_billing_account is set."
  type        = number
  default     = 5
}
