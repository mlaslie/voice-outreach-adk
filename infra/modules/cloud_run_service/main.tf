# One Cloud Run (v2) service with the settings this system relies on.

variable "project_id" { type = string }
variable "region" { type = string }
variable "name" { type = string }
variable "image" { type = string }
variable "service_account" { type = string }
variable "env" {
  type    = map(string)
  default = {}
}
variable "public" {
  description = "allUsers may invoke (only for the gateway: Twilio cannot present Google credentials)."
  type        = bool
  default     = false
}
variable "invokers" {
  type    = list(string)
  default = []
}
variable "min_instances" {
  type    = number
  default = 0
}
variable "max_instances" {
  type    = number
  default = 10
}
variable "concurrency" {
  type    = number
  default = 80
}
variable "timeout_s" {
  type    = number
  default = 300
}
variable "cpu" {
  type    = string
  default = "1"
}
variable "memory" {
  type    = string
  default = "512Mi"
}
variable "cpu_always_on" {
  description = "No CPU throttling between requests (needed for long-lived WebSockets with background tasks)."
  type        = bool
  default     = false
}
variable "session_affinity" {
  type    = bool
  default = false
}
variable "iap" {
  description = "Put Cloud Run's built-in IAP in front of the service."
  type        = bool
  default     = false
}

resource "google_cloud_run_v2_service" "this" {
  project             = var.project_id
  location            = var.region
  name                = var.name
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false
  iap_enabled         = var.iap
  launch_stage        = var.iap ? "BETA" : null
  # Public services still require a binding below; this only removes the default 403 page.
  invoker_iam_disabled = false

  template {
    service_account                  = var.service_account
    timeout                          = "${var.timeout_s}s"
    max_instance_request_concurrency = var.concurrency
    session_affinity                 = var.session_affinity
    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }
    containers {
      image = var.image
      resources {
        limits            = { cpu = var.cpu, memory = var.memory }
        cpu_idle          = !var.cpu_always_on
        startup_cpu_boost = true
      }
      dynamic "env" {
        for_each = var.env
        content {
          name  = env.key
          value = env.value
        }
      }
      startup_probe {
        http_get {
          path = "/healthz"
        }
        period_seconds    = 2
        failure_threshold = 30
      }
      liveness_probe {
        http_get {
          path = "/healthz"
        }
        period_seconds = 30
      }
    }
  }
}

resource "google_cloud_run_v2_service_iam_member" "public" {
  count    = var.public ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.this.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "invokers" {
  for_each = toset(var.invokers)
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.this.name
  role     = "roles/run.invoker"
  member   = each.value
}

output "url" {
  value = google_cloud_run_v2_service.this.uri
}

output "name" {
  value = google_cloud_run_v2_service.this.name
}
