output "instance_name" {
  description = "POMA VM instance name."
  value       = google_compute_instance.poma.name
}

output "zone" {
  description = "POMA VM zone."
  value       = google_compute_instance.poma.zone
}

output "region" {
  description = "POMA VM region."
  value       = var.region
}

output "external_ip" {
  description = "Ephemeral external IP used for outbound connectivity."
  value       = google_compute_instance.poma.network_interface[0].access_config[0].nat_ip
}

output "ssh_command" {
  description = "IAP SSH command for manual access."
  value       = "gcloud compute ssh ${google_compute_instance.poma.name} --zone ${google_compute_instance.poma.zone} --tunnel-through-iap"
}
