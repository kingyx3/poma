locals {
  app_user = "poma"
  app_dir  = "/opt/poma"
  labels = {
    app       = "poma"
    component = "trading-vm"
  }
}

resource "google_project_service" "required" {
  for_each = toset([
    "billingbudgets.googleapis.com",
    "compute.googleapis.com",
    "iap.googleapis.com",
  ])

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_compute_network" "poma" {
  name                    = "${var.instance_name}-network"
  auto_create_subnetworks = false

  depends_on = [google_project_service.required]
}

resource "google_compute_subnetwork" "poma" {
  name          = "${var.instance_name}-subnet"
  ip_cidr_range = var.network_cidr
  network       = google_compute_network.poma.id
  region        = var.region
}

resource "google_compute_firewall" "iap_ssh" {
  name    = "${var.instance_name}-iap-ssh"
  network = google_compute_network.poma.name

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["poma-vm"]
}

resource "google_compute_instance" "poma" {
  name         = var.instance_name
  machine_type = "e2-micro"
  zone         = var.zone
  labels       = local.labels
  tags         = ["poma-vm"]

  boot_disk {
    initialize_params {
      image = "ubuntu-os-cloud/ubuntu-2204-lts"
      size  = var.boot_disk_size_gb
      type  = "pd-standard"
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.poma.id

    access_config {
      # Ephemeral public IP for outbound package installs and market/data-provider traffic.
    }
  }

  metadata = {
    block-project-ssh-keys = "true"
    startup-script = templatefile("${path.module}/startup.sh", {
      app_user = local.app_user
      app_dir  = local.app_dir
    })
  }

  scheduling {
    automatic_restart   = true
    on_host_maintenance = "MIGRATE"
    preemptible         = false
  }

  shielded_instance_config {
    enable_integrity_monitoring = true
    enable_vtpm                 = true
  }

  service_account {
    scopes = [
      "https://www.googleapis.com/auth/logging.write",
      "https://www.googleapis.com/auth/monitoring.write",
    ]
  }

  depends_on = [google_compute_firewall.iap_ssh]
}
