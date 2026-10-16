variable "project_id" { type = string }
variable "location" { type = string }
variable "name" { type = string }
variable "delete_after_days" {
  description = "Lifecycle delete age; 0 disables the rule."
  type        = number
  default     = 0
}
variable "force_destroy" {
  type    = bool
  default = false
}
variable "iam" {
  description = "role => list of members, bucket-scoped"
  type        = map(list(string))
  default     = {}
}

resource "google_storage_bucket" "this" {
  project                     = var.project_id
  name                        = var.name
  location                    = var.location
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = var.force_destroy

  versioning {
    enabled = true
  }

  dynamic "lifecycle_rule" {
    for_each = var.delete_after_days > 0 ? [1] : []
    content {
      condition {
        age = var.delete_after_days
      }
      action {
        type = "Delete"
      }
    }
  }

  # Old noncurrent versions go after 7 days so versioning doesn't keep audio forever.
  lifecycle_rule {
    condition {
      days_since_noncurrent_time = 7
    }
    action {
      type = "Delete"
    }
  }
}

locals {
  bindings = flatten([for role, members in var.iam : [for m in members : { role = role, member = m }]])
}

resource "google_storage_bucket_iam_member" "this" {
  for_each = { for b in local.bindings : "${b.role}|${b.member}" => b }
  bucket   = google_storage_bucket.this.name
  role     = each.value.role
  member   = each.value.member
}

output "name" {
  value = google_storage_bucket.this.name
}
