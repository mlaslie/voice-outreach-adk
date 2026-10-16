# One service account per workload, each with the minimum project-level roles.
# Resource-scoped grants (one bucket, one secret) live next to those resources.

variable "project_id" { type = string }

variable "accounts" {
  description = "account_id => { display_name, project_roles }"
  type = map(object({
    display_name  = string
    project_roles = list(string)
  }))
}

resource "google_service_account" "this" {
  for_each     = var.accounts
  project      = var.project_id
  account_id   = each.key
  display_name = each.value.display_name
}

locals {
  bindings = flatten([
    for id, acct in var.accounts : [
      for role in acct.project_roles : { key = "${id}|${role}", id = id, role = role }
    ]
  ])
}

resource "google_project_iam_member" "this" {
  for_each = { for b in local.bindings : b.key => b }
  project  = var.project_id
  role     = each.value.role
  member   = "serviceAccount:${google_service_account.this[each.value.id].email}"
}

output "emails" {
  value = { for id, sa in google_service_account.this : id => sa.email }
}

output "members" {
  value = { for id, sa in google_service_account.this : id => "serviceAccount:${sa.email}" }
}
