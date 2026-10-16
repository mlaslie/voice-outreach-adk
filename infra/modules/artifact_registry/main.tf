variable "project_id" { type = string }
variable "location" { type = string }
variable "repository_id" { type = string }
variable "readers" {
  type    = list(string)
  default = []
}
variable "writers" {
  type    = list(string)
  default = []
}

resource "google_artifact_registry_repository" "this" {
  project       = var.project_id
  location      = var.location
  repository_id = var.repository_id
  format        = "DOCKER"

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 20
    }
  }
}

resource "google_artifact_registry_repository_iam_member" "readers" {
  for_each   = toset(var.readers)
  project    = var.project_id
  location   = var.location
  repository = google_artifact_registry_repository.this.repository_id
  role       = "roles/artifactregistry.reader"
  member     = each.value
}

resource "google_artifact_registry_repository_iam_member" "writers" {
  for_each   = toset(var.writers)
  project    = var.project_id
  location   = var.location
  repository = google_artifact_registry_repository.this.repository_id
  role       = "roles/artifactregistry.writer"
  member     = each.value
}

output "url" {
  value = "${var.location}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.this.repository_id}"
}
