variable "project_id" { type = string }
variable "location_id" { type = string }
variable "backup_retention_days" {
  type    = number
  default = 7
}
variable "delete_protection" {
  type    = bool
  default = true
}

resource "google_firestore_database" "default" {
  project                           = var.project_id
  name                              = "(default)"
  location_id                       = var.location_id
  type                              = "FIRESTORE_NATIVE"
  concurrency_mode                  = "OPTIMISTIC"
  app_engine_integration_mode       = "DISABLED"
  point_in_time_recovery_enablement = "POINT_IN_TIME_RECOVERY_ENABLED"
  delete_protection_state           = var.delete_protection ? "DELETE_PROTECTION_ENABLED" : "DELETE_PROTECTION_DISABLED"
  deletion_policy                   = var.delete_protection ? "ABANDON" : "DELETE"
}

resource "google_firestore_backup_schedule" "daily" {
  project   = var.project_id
  database  = google_firestore_database.default.name
  retention = "${var.backup_retention_days * 86400}s"
  daily_recurrence {}
}

output "database" {
  value = google_firestore_database.default.name
}
