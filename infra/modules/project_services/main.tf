variable "project_id" { type = string }
variable "services" { type = set(string) }

resource "google_project_service" "this" {
  for_each = var.services
  project  = var.project_id
  service  = each.value

  # Never disable an API on destroy: other workloads in a shared project may use it.
  disable_on_destroy         = false
  disable_dependent_services = false
}

output "enabled" {
  value = [for s in google_project_service.this : s.service]
}
