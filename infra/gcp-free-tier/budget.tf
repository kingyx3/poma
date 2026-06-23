data "google_project" "poma" {
  project_id = var.project_id
}

resource "google_billing_budget" "poma" {
  provider        = google-beta
  count           = var.budget_billing_account == "" ? 0 : 1
  billing_account = var.budget_billing_account
  display_name    = "${var.instance_name}-monthly-budget"

  budget_filter {
    projects = ["projects/${data.google_project.poma.number}"]
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(var.monthly_budget_usd)
    }
  }

  threshold_rules {
    threshold_percent = 0.5
  }

  threshold_rules {
    threshold_percent = 0.9
  }

  threshold_rules {
    threshold_percent = 1.0
  }

  all_updates_rule {
    disable_default_iam_recipients = false
  }
}
