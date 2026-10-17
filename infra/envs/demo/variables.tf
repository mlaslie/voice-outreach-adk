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

# --------------------------------------------------------------------------- services

variable "gateway_image" {
  description = "Full image reference for voa-gateway (scripts/build.sh prints the tag). Empty = not deployed."
  type        = string
  default     = ""
}

variable "api_image" {
  type    = string
  default = ""
}

variable "ui_image" {
  type    = string
  default = ""
}

variable "gateway_min_instances" {
  description = "Keep one warm instance so an answered call never waits on a cold start."
  type        = number
  default     = 1
}

variable "dial_concurrency" {
  description = "Simultaneous outbound dials (Cloud Tasks max concurrent dispatches)."
  type        = number
  default     = 1
}

variable "org_name" {
  type    = string
  default = "Acme Health"
}

variable "support_email" {
  type    = string
  default = "support@example.org"
}

variable "clinic_timezone" {
  type    = string
  default = "America/New_York"
}

variable "twilio_from_number" {
  description = "Caller ID for outbound calls (E.164). Identifying: set only in terraform.tfvars."
  type        = string
  default     = ""
}

variable "softphone_enabled" {
  description = "Allow browser test calls into the gateway (tokens are minted by the API behind IAP)."
  type        = bool
  default     = true
}

variable "iap_members" {
  description = "Who may open the dashboard, e.g. [\"user:nurse@example.org\"]."
  type        = list(string)
  default     = []
}

variable "static_allowlist" {
  description = "Comma-separated E.164 numbers always dialable (identifying: tfvars only). Usually empty; manage numbers in the dashboard."
  type        = string
  default     = ""
}

variable "operators" {
  description = "People who may impersonate the dashboard SA (local UI dev, e2e tests), e.g. [\"user:you@example.org\"]. Identifying: tfvars only."
  type        = list(string)
  default     = []
}

variable "alert_emails" {
  description = "Who gets alert e-mails (identifying: tfvars only)."
  type        = list(string)
  default     = []
}

variable "sms_dry_run" {
  description = "Log SMS instead of sending. Keep true until the number, A2P campaign and copy are confirmed."
  type        = bool
  default     = true
}

variable "sms_from_number" {
  description = "SMS sender number (E.164; identifying: tfvars only). Usually a dedicated number (see PLAN 11.9)."
  type        = string
  default     = ""
}
