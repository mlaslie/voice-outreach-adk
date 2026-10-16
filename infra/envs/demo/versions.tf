terraform {
  required_version = ">= 1.9"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.7"
    }
  }
  # Partial config: bucket/prefix come from backend.hcl (gitignored), created by
  # scripts/bootstrap/00-state-bucket.sh.  terraform init -backend-config=backend.hcl
  backend "gcs" {}
}

provider "google" {
  project               = var.project_id
  region                = var.region
  user_project_override = true
  billing_project       = var.project_id
  default_labels        = var.labels
}
