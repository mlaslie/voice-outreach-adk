variable "project_id" {
  description = "GCP project to deploy into (must already exist with billing enabled)."
  type        = string
}

variable "region" {
  description = "Region for Cloud Run, Artifact Registry, buckets. Must serve the Gemini Live model."
  type        = string
  default     = "us-central1"
}

variable "firestore_location" {
  description = "Firestore location (multi-region nam5 or a region). Cannot be changed later."
  type        = string
  default     = "nam5"
}

variable "name_prefix" {
  description = "Prefix for service accounts, buckets and other named resources."
  type        = string
  default     = "voa"
}

variable "recordings_retention_days" {
  description = "Call recordings are deleted after this many days (0 = keep)."
  type        = number
  default     = 90
}

variable "protect_data" {
  description = "Delete protection on Firestore and no force-destroy on buckets. Set false only for throwaway test projects."
  type        = bool
  default     = true
}

variable "enable_data_access_audit_logs" {
  description = "Data Access audit logs for Firestore, Cloud Storage and Secret Manager (who read PHI)."
  type        = bool
  default     = true
}

variable "billing_account_id" {
  description = "Billing account id for the budget alert (XXXXXX-XXXXXX-XXXXXX). Empty = no budget."
  type        = string
  default     = ""
}

variable "monthly_budget_usd" {
  type    = number
  default = 200
}

variable "labels" {
  type    = map(string)
  default = { app = "voice-outreach", managed-by = "terraform" }
}
