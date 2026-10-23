# Cloud Run services, task queues and build staging (phases 2-4).
# Service URLs are deterministic (https://<name>-<project number>.<region>.run.app),
# which breaks the gateway <-> api reference cycle.

locals {
  url_suffix  = "${data.google_project.this.number}.${var.region}.run.app"
  gateway_url = "https://${local.p}-gateway-${local.url_suffix}"
  api_url     = "https://${local.p}-api-${local.url_suffix}"
  ui_url      = "https://${local.p}-ui-${local.url_suffix}"
  admin_url   = "https://${local.p}-admin-${local.url_suffix}"

  common_env = {
    VOA_ENV                          = "demo"
    LOG_LEVEL                        = "INFO"
    VOA__LOG_LEVEL                   = "INFO"
    VOA__GCP__PROJECT_ID             = var.project_id
    VOA__GCP__REGION                 = var.region
    VOA__SITE__ORG_NAME              = var.org_name
    VOA__SITE__SUPPORT_EMAIL         = var.support_email
    VOA__SITE__TIMEZONE              = var.clinic_timezone
    VOA__SERVICES__GATEWAY_URL       = local.gateway_url
    VOA__SERVICES__API_URL           = local.api_url
    VOA__SERVICES__TASKS_INVOKER_SA  = module.sa.emails["${local.p}-tasks-invoker"]
    VOA__SERVICES__UI_SA             = module.sa.emails["${local.p}-ui"]
    VOA__SERVICES__ADMIN_UI_SA       = module.sa.emails["${local.p}-admin"]
    VOA__SERVICES__NURSE_AGENT_SA    = module.sa.emails["${local.p}-nurse"]
    VOA__DIALING__ALLOWLIST          = var.static_allowlist
    VOA__SMS__DRY_RUN                = tostring(var.sms_dry_run)
    VOA__SMS__SURVEY_ENABLED         = tostring(var.sms_survey_enabled)
    VOA__LANGUAGES__AUTO_APPROVE     = tostring(var.languages_auto_approve) # demo only
    VOA__SMS__FROM_NUMBER            = var.sms_from_number
    VOA__SMS__VOICE_NUMBER           = var.twilio_from_number
    VOA__SERVICES__RECORDINGS_BUCKET = module.recordings.name
    VOA__SERVICES__PROMPTS_BUCKET    = module.prompts.name
    VOA__SERVICES__BRANDING_BUCKET   = module.branding.name
    VOA__SERVICES__INGEST_SA         = module.sa.emails["${local.p}-ingest"]
    VOA__TELEPHONY__VOICE_PROVIDER   = "twilio"
    # Real texts still need sms_dry_run = false.
    VOA__TELEPHONY__SMS_PROVIDER           = "twilio"
    VOA__PROVIDERS__TWILIO__AUTH_TOKEN     = "secret://twilio-auth-token"
    VOA__PROVIDERS__TWILIO__ACCOUNT_SID    = "secret://twilio-account-sid"
    VOA__PROVIDERS__TWILIO__API_KEY_SID    = "secret://twilio-api-key-sid"
    VOA__PROVIDERS__TWILIO__API_KEY_SECRET = "secret://twilio-api-key-secret"
    VOA__PROVIDERS__TWILIO__FROM_NUMBER    = var.twilio_from_number
    VOA__SOFTPHONE__ENABLED                = tostring(var.softphone_enabled)
    VOA__SOFTPHONE__TOKEN_SECRET           = "secret://softphone-token-key"
  }
}

module "build_staging" {
  source            = "../../modules/storage"
  project_id        = var.project_id
  location          = var.region
  name              = "${var.project_id}-${local.p}-build"
  delete_after_days = 14
  force_destroy     = true
  iam = {
    "roles/storage.objectAdmin" = [local.build]
  }
  depends_on = [module.services]
}

# --------------------------------------------------------------------------- queues

# The dial queue is not the cap on simultaneous calls: a dial task only places the
# call and returns; the call itself then runs for minutes. The cap is the API's
# dialing.max_concurrent_calls (var.max_concurrent_calls). With no free slot the
# dial task re-enqueues itself a few seconds later and answers 200, so the dial
# queue's retries are for real errors only: bounded, and short enough that the
# API's stale-run check (routes_tasks.DIAL_TASK_RETRY_WINDOW_S = 900) outlasts
# them. Keep max_retry_duration and that constant in step.
# Rate 1/s keeps dials within Twilio's default 1 call per second.
resource "google_cloud_tasks_queue" "this" {
  for_each = {
    dial = {
      concurrent = var.dial_concurrency, rate = 1
      retry      = { attempts = 5, min = "10s", max = "120s", doublings = 3, duration = "900s" }
    }
    events = {
      concurrent = 10, rate = 10
      retry      = { attempts = 5, min = "5s", max = "300s", doublings = 4, duration = "3600s" }
    }
    sms = {
      concurrent = 5, rate = 5
      retry      = { attempts = 5, min = "5s", max = "300s", doublings = 4, duration = "3600s" }
    }
  }
  project  = var.project_id
  location = var.region
  name     = "${local.p}-${each.key}"
  rate_limits {
    max_concurrent_dispatches = each.value.concurrent
    max_dispatches_per_second = each.value.rate
  }
  retry_config {
    max_attempts       = each.value.retry.attempts
    min_backoff        = each.value.retry.min
    max_backoff        = each.value.retry.max
    max_doublings      = each.value.retry.doublings
    max_retry_duration = each.value.retry.duration
  }
  lifecycle {
    precondition {
      condition     = var.dial_concurrency <= var.max_concurrent_calls
      error_message = "dial_concurrency must not exceed max_concurrent_calls (see variables.tf)."
    }
  }
  depends_on = [module.services]
}

resource "google_cloud_tasks_queue_iam_member" "enqueuers" {
  for_each = { for pair in setproduct(keys(google_cloud_tasks_queue.this), [local.gateway, local.api]) : "${pair[0]}|${pair[1]}" => pair }
  project  = var.project_id
  location = var.region
  name     = google_cloud_tasks_queue.this[each.value[0]].name
  role     = "roles/cloudtasks.enqueuer"
  member   = each.value[1]
}

# Creating an OIDC task requires acting as the token's service account.
resource "google_service_account_iam_member" "gateway_acts_as_invoker" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${module.sa.emails["${local.p}-tasks-invoker"]}"
  role               = "roles/iam.serviceAccountUser"
  member             = local.gateway
}

# --------------------------------------------------------------------------- services

module "gateway" {
  count           = var.gateway_image == "" ? 0 : 1
  source          = "../../modules/cloud_run_service"
  project_id      = var.project_id
  region          = var.region
  name            = "${local.p}-gateway"
  image           = var.gateway_image
  service_account = module.sa.emails["${local.p}-gateway"]
  public          = true # Twilio webhooks + media streams; every route is signature-checked
  min_instances   = var.gateway_min_instances
  # Capacity: each call holds one Twilio media WebSocket here (one request toward
  # `concurrency`) plus an outbound Gemini Live session, short webhooks aside.
  # Transcoding is audioop (C) and the recording is 8 kHz PCM in memory (~1 MB
  # per call-minute), so one 2 vCPU / 2 GiB instance carries max_concurrent_calls
  # = 3 with room to spare; 10 x 8 = 80 is far above any dial limit. Raise
  # max_instances only if max_concurrent_calls goes past ~60. docs/RUNBOOK.md
  # "Scaling calls".
  max_instances    = 10
  concurrency      = 8 # simultaneous calls per instance (audio is CPU-light; Live is remote)
  timeout_s        = 3600
  cpu              = "2"
  memory           = "2Gi"
  cpu_always_on    = true
  session_affinity = true
  env              = local.common_env
  depends_on       = [module.secrets, google_cloud_tasks_queue.this]
}

module "api" {
  count           = var.api_image == "" ? 0 : 1
  source          = "../../modules/cloud_run_service"
  project_id      = var.project_id
  region          = var.region
  name            = "${local.p}-api"
  image           = var.api_image
  service_account = module.sa.emails["${local.p}-api"]
  public          = false
  # Index-keyed bindings: append new callers at the END, or existing ones are
  # replaced (briefly revoked) on apply.
  invokers      = [module.sa.members["${local.p}-ui"], module.sa.members["${local.p}-tasks-invoker"], module.sa.members["${local.p}-nurse"], module.sa.members["${local.p}-admin"], module.sa.members["${local.p}-ingest"]]
  min_instances = var.api_min_instances
  max_instances = 5
  concurrency   = 40
  timeout_s     = 3600 # SSE streams to the dashboard
  memory        = "1Gi"
  env = merge(local.common_env, {
    VOA__DIALING__MAX_CONCURRENT_CALLS = tostring(var.max_concurrent_calls)
  })
  depends_on = [module.secrets, google_cloud_tasks_queue.this]
}

resource "google_cloud_scheduler_job" "dispatch_due" {
  count       = var.api_image == "" ? 0 : 1
  project     = var.project_id
  region      = var.region
  name        = "${local.p}-dispatch-due"
  description = "Dial scheduled call runs and due call-list entries"
  schedule    = "* * * * *"
  time_zone   = "Etc/UTC"
  # Pausing this job is the off switch for scheduled dialling.
  attempt_deadline = "60s"
  http_target {
    http_method = "POST"
    uri         = "${local.api_url}/cron/dispatch-due"
    oidc_token {
      service_account_email = module.sa.emails["${local.p}-tasks-invoker"]
      audience              = local.api_url
    }
  }
  depends_on = [module.services]
}

# Operators can mint ID tokens as the dashboard SA to exercise the private API
# exactly as the dashboard does (local UI development, deployed e2e tests).
# Operators can act as the EHR feed (sample feed script, integration tests).
resource "google_service_account_iam_member" "operators_impersonate_ingest" {
  for_each           = toset(var.operators)
  service_account_id = "projects/${var.project_id}/serviceAccounts/${module.sa.emails["${local.p}-ingest"]}"
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = each.value
}

resource "google_service_account_iam_member" "operators_impersonate_admin" {
  for_each           = toset(var.operators)
  service_account_id = "projects/${var.project_id}/serviceAccounts/${module.sa.emails["${local.p}-admin"]}"
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = each.value
}

resource "google_service_account_iam_member" "operators_impersonate_ui" {
  for_each           = toset(var.operators)
  service_account_id = "projects/${var.project_id}/serviceAccounts/${module.sa.emails["${local.p}-ui"]}"
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = each.value
}

# --------------------------------------------------------------------------- dashboard (IAP)

resource "google_project_service_identity" "iap" {
  provider   = google-beta
  project    = var.project_id
  service    = "iap.googleapis.com"
  depends_on = [module.services]
}

module "ui" {
  count           = var.ui_image == "" ? 0 : 1
  source          = "../../modules/cloud_run_service"
  project_id      = var.project_id
  region          = var.region
  name            = "${local.p}-ui"
  image           = var.ui_image
  service_account = module.sa.emails["${local.p}-ui"]
  public          = false
  iap             = true
  # Only IAP's service agent may invoke the service; people go through IAP.
  invokers      = ["serviceAccount:${google_project_service_identity.iap.email}"]
  min_instances = var.ui_min_instances
  max_instances = 3
  concurrency   = 80
  timeout_s     = 3600 # SSE passthrough
  memory        = "512Mi"
  env = {
    API_BASE_URL      = local.api_url
    NODE_ENV          = "production"
    ADMIN_CONSOLE_URL = var.admin_image == "" ? "" : local.admin_url
  }
}

resource "google_iap_web_cloud_run_service_iam_member" "ui_users" {
  for_each               = var.ui_image == "" ? toset([]) : toset(var.iap_members)
  project                = var.project_id
  location               = var.region
  cloud_run_service_name = module.ui[0].name
  role                   = "roles/iap.httpsResourceAccessor"
  member                 = each.value
}

output "ui_url" {
  value = var.ui_image == "" ? null : module.ui[0].url
}

# --------------------------------------------------------------------------- admin console (IAP)
# The same dashboard code built with APP_MODE=admin: configuration only (schedules,
# patients, authorized numbers, messaging, display). Its own identity, which the
# API's route policy alone allows to change configuration, and its own IAP list.

module "admin" {
  count           = var.admin_image == "" ? 0 : 1
  source          = "../../modules/cloud_run_service"
  project_id      = var.project_id
  region          = var.region
  name            = "${local.p}-admin"
  image           = var.admin_image
  service_account = module.sa.emails["${local.p}-admin"]
  public          = false
  iap             = true
  invokers        = ["serviceAccount:${google_project_service_identity.iap.email}"]
  min_instances   = var.admin_min_instances
  max_instances   = 2
  concurrency     = 80
  timeout_s       = 3600
  memory          = "512Mi"
  env = {
    API_BASE_URL      = local.api_url
    NODE_ENV          = "production"
    NURSE_CONSOLE_URL = var.ui_image == "" ? "" : local.ui_url
    ANALYTICS_URL     = var.analytics_image == "" ? "" : "https://${local.p}-analytics-${local.url_suffix}"
  }
}

resource "google_iap_web_cloud_run_service_iam_member" "admin_users" {
  for_each               = var.admin_image == "" ? toset([]) : toset(var.iap_admin_members)
  project                = var.project_id
  location               = var.region
  cloud_run_service_name = module.admin[0].name
  role                   = "roles/iap.httpsResourceAccessor"
  member                 = each.value
}

output "admin_url" {
  value = var.admin_image == "" ? null : module.admin[0].url
}

# --------------------------------------------------------------------------- observability

module "observability" {
  source       = "../../modules/observability"
  project_id   = var.project_id
  prefix       = local.p
  gateway_host = var.gateway_image == "" ? "" : trimprefix(local.gateway_url, "https://")
  alert_emails = var.alert_emails
  depends_on   = [module.services]
}


# --------------------------------------------------------------------------- SMS follow-up

resource "google_cloud_scheduler_job" "sms_expire" {
  count            = var.api_image == "" ? 0 : 1
  project          = var.project_id
  region           = var.region
  name             = "${local.p}-sms-expire"
  description      = "Expire stale SMS conversations (files abandoned surveys with urgent findings)"
  schedule         = "*/10 * * * *"
  time_zone        = "Etc/UTC"
  attempt_deadline = "120s"
  http_target {
    http_method = "POST"
    uri         = "${local.api_url}/cron/sms-expire"
    oidc_token {
      service_account_email = module.sa.emails["${local.p}-tasks-invoker"]
      audience              = local.api_url
    }
  }
  depends_on = [module.services]
}

# Inbound SMS de-duplication markers expire on their own (review C-7 store).
resource "google_firestore_field" "sms_inbound_seen_ttl" {
  project    = var.project_id
  database   = module.firestore.database
  collection = "sms_inbound_seen"
  field      = "expire_at"
  ttl_config {}
  index_config {}
}

# "Test by text" (the SMS simulator) writes fictional test data into simulator__*
# collections. Each document carries expire_at (now + sms.simulator_ttl_days,
# default 7, set by SimulatorStore on every write), and these policies delete it.
# A TTL policy is per collection group (no wildcard), so this is a list: it must
# equal sms_simulator.WRITTEN_COLLECTIONS (services/api/tests/
# test_sms_simulator_expiry.py checks it, and that the simulator writes nowhere else).
locals {
  simulator_ttl_collections = [
    "calls",
    "call_list",
    "ehr_outbox",
    "rate_limits",
    "reports",
    "scheduled_callbacks",
    "sms_consents",
    "sms_filings",
    "sms_inbound_seen",
    "sms_messages",
    "sms_optouts",
    "sms_sessions",
  ]
}

resource "google_firestore_field" "simulator_ttl" {
  for_each   = toset(local.simulator_ttl_collections)
  project    = var.project_id
  database   = module.firestore.database
  collection = "simulator__${each.value}"
  field      = "expire_at"
  ttl_config {}
  index_config {}
}


# --------------------------------------------------------------------------- nurse assistant (A2A for Gemini Enterprise)

locals {
  nurse_url = "https://${local.p}-nurse-${local.url_suffix}"
}

# Gemini Enterprise calls the agent as this project's Discovery Engine service agent.
resource "google_project_service_identity" "discoveryengine" {
  provider   = google-beta
  project    = var.project_id
  service    = "discoveryengine.googleapis.com"
  depends_on = [module.services]
}

module "nurse" {
  count           = var.nurse_image == "" ? 0 : 1
  source          = "../../modules/cloud_run_service"
  project_id      = var.project_id
  region          = var.region
  name            = "${local.p}-nurse"
  image           = var.nurse_image
  service_account = module.sa.emails["${local.p}-nurse"]
  public          = false
  invokers = concat(
    ["serviceAccount:${google_project_service_identity.discoveryengine.email}"],
    var.nurse_extra_invokers,
  )
  min_instances = var.nurse_min_instances
  max_instances = 5
  concurrency   = 20
  timeout_s     = 300
  memory        = "1Gi"
  env = merge(local.common_env, {
    VOA_NURSE_PUBLIC_URL      = local.nurse_url
    VOA_NURSE_DASHBOARD_URL   = var.ui_image == "" ? "" : module.ui[0].url
    VOA_NURSE_ALLOWED_EMAILS  = join(",", [for m in var.iap_members : trimprefix(m, "user:") if startswith(m, "user:")])
    VOA_NURSE_OAUTH_CLIENT_ID = var.nurse_oauth_client_id
  })
  depends_on = [module.secrets]
}

resource "google_firestore_field" "nurse_ttl" {
  for_each   = toset(["nurse_sessions", "nurse_proposals"])
  project    = var.project_id
  database   = module.firestore.database
  collection = each.value
  field      = "expire_at"
  ttl_config {}
  index_config {}
}

output "nurse_url" {
  value = var.nurse_image == "" ? null : local.nurse_url
}
