# Creates EMPTY secrets and per-secret accessor bindings. Secret values are never
# in Terraform (they would land in state); scripts/set-secrets.sh adds versions.

variable "project_id" { type = string }
variable "secrets" {
  description = "secret_id => list of members granted secretAccessor on that secret only"
  type        = map(list(string))
}

resource "google_secret_manager_secret" "this" {
  for_each  = var.secrets
  project   = var.project_id
  secret_id = each.key
  replication {
    auto {}
  }
}

locals {
  bindings = flatten([for id, members in var.secrets : [for m in members : { id = id, member = m }]])
}

resource "google_secret_manager_secret_iam_member" "this" {
  for_each  = { for b in local.bindings : "${b.id}|${b.member}" => b }
  project   = var.project_id
  secret_id = google_secret_manager_secret.this[each.value.id].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = each.value.member
}

output "ids" {
  value = [for s in google_secret_manager_secret.this : s.secret_id]
}
