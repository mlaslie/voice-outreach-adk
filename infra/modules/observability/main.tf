# Log-based metrics, alerts and an uptime check for the voice outreach services.
# Metrics key off the structured `event` field every service logs.

variable "project_id" { type = string }
variable "prefix" { type = string }
variable "gateway_host" {
  description = "Gateway hostname for the uptime check (empty = no check)."
  type        = string
  default     = ""
}
variable "alert_emails" {
  type    = list(string)
  default = []
}

locals {
  metrics = {
    live_errors     = { filter = "jsonPayload.event=\"live.error\"", desc = "run_live loop failed during a call" }
    drain_timeouts  = { filter = "jsonPayload.event=\"drain.timeout\"", desc = "goodbye playout not confirmed before hangup" }
    dial_failures   = { filter = "jsonPayload.event=(\"dial.rejected\" OR \"dial.error\")", desc = "provider refused or unreachable" }
    invalid_reports = { filter = "jsonPayload.event=\"report.invalid\"", desc = "a call report failed validation" }
    http_errors     = { filter = "jsonPayload.event=\"http.error\"", desc = "unhandled exception in a request" }
    no_twilio_token = { filter = "jsonPayload.event=(\"twilio.no_token\" OR \"secret.unavailable\")", desc = "a secret is missing; webhooks are being refused" }
  }
}

resource "google_logging_metric" "this" {
  for_each    = local.metrics
  project     = var.project_id
  name        = "${var.prefix}_${each.key}"
  description = each.value.desc
  filter      = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name:\"${var.prefix}-\" AND ${each.value.filter}"
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }
}

resource "google_monitoring_notification_channel" "email" {
  for_each     = toset(var.alert_emails)
  project      = var.project_id
  display_name = "${var.prefix} alerts"
  type         = "email"
  labels       = { email_address = each.value }
}

resource "google_monitoring_alert_policy" "metric" {
  for_each     = google_logging_metric.this
  project      = var.project_id
  display_name = "${var.prefix}: ${each.key}"
  combiner     = "OR"
  conditions {
    display_name = "${each.key} > 0 in 5 min"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${each.value.name}\" AND resource.type=\"cloud_run_revision\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
  notification_channels = [for c in google_monitoring_notification_channel.email : c.id]
  documentation {
    content   = "See docs/RUNBOOK.md, section for `${each.key}`. ${local.metrics[each.key].desc}."
    mime_type = "text/markdown"
  }
}

resource "google_monitoring_uptime_check_config" "gateway" {
  count        = var.gateway_host == "" ? 0 : 1
  project      = var.project_id
  display_name = "${var.prefix}-gateway health"
  timeout      = "10s"
  period       = "300s"
  http_check {
    path         = "/health"
    port         = 443
    use_ssl      = true
    validate_ssl = true
  }
  monitored_resource {
    type   = "uptime_url"
    labels = { project_id = var.project_id, host = var.gateway_host }
  }
}

resource "google_monitoring_alert_policy" "gateway_down" {
  count        = var.gateway_host == "" ? 0 : 1
  project      = var.project_id
  display_name = "${var.prefix}: gateway health check failing"
  combiner     = "OR"
  conditions {
    display_name = "uptime check failing"
    condition_threshold {
      filter          = "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND resource.type=\"uptime_url\" AND metric.label.check_id=\"${google_monitoring_uptime_check_config.gateway[0].uptime_check_id}\""
      comparison      = "COMPARISON_LT"
      threshold_value = 1
      duration        = "600s"
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_FRACTION_TRUE"
        cross_series_reducer = "REDUCE_MEAN"
        group_by_fields      = ["resource.label.host"]
      }
    }
  }
  notification_channels = [for c in google_monitoring_notification_channel.email : c.id]
}
