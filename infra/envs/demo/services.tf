# Cloud Run services, task queues and build staging (phases 2-4).
# Service URLs are deterministic (https://<name>-<project number>.<region>.run.app),
# which breaks the gateway <-> api reference cycle.

locals {
  url_suffix  = "${data.google_project.this.number}.${var.region}.run.app"
  gateway_url = "https://${local.p}-gateway-${local.url_suffix}"
  api_url     = "https://${local.p}-api-${local.url_suffix}"

  common_env = {
    VOA_ENV                                = "demo"
    LOG_LEVEL                              = "INFO"
    VOA__LOG_LEVEL                         = "INFO"
    VOA__GCP__PROJECT_ID                   = var.project_id
    VOA__GCP__REGION                       = var.region
    VOA__SITE__ORG_NAME                    = var.org_name
    VOA__SITE__SUPPORT_EMAIL               = var.support_email
    VOA__SITE__TIMEZONE                    = var.clinic_timezone
    VOA__SERVICES__GATEWAY_URL             = local.gateway_url
    VOA__SERVICES__API_URL                 = local.api_url
    VOA__SERVICES__TASKS_INVOKER_SA        = module.sa.emails["${local.p}-tasks-invoker"]
    VOA__SERVICES__RECORDINGS_BUCKET       = module.recordings.name
    VOA__SERVICES__PROMPTS_BUCKET          = module.prompts.name
    VOA__TELEPHONY__VOICE_PROVIDER         = "twilio"
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

resource "google_cloud_tasks_queue" "this" {
  for_each = {
    dial   = { concurrent = var.dial_concurrency, rate = 1 }
    events = { concurrent = 10, rate = 10 }
    sms    = { concurrent = 5, rate = 5 }
  }
  project  = var.project_id
  location = var.region
  name     = "${local.p}-${each.key}"
  rate_limits {
    max_concurrent_dispatches = each.value.concurrent
    max_dispatches_per_second = each.value.rate
  }
  retry_config {
    max_attempts       = 5
    min_backoff        = "5s"
    max_backoff        = "300s"
    max_doublings      = 4
    max_retry_duration = "3600s"
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
  count            = var.gateway_image == "" ? 0 : 1
  source           = "../../modules/cloud_run_service"
  project_id       = var.project_id
  region           = var.region
  name             = "${local.p}-gateway"
  image            = var.gateway_image
  service_account  = module.sa.emails["${local.p}-gateway"]
  public           = true # Twilio webhooks + media streams; every route is signature-checked
  min_instances    = var.gateway_min_instances
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
  invokers        = [module.sa.members["${local.p}-ui"], module.sa.members["${local.p}-tasks-invoker"]]
  min_instances   = 0
  max_instances   = 5
  concurrency     = 40
  timeout_s       = 3600 # SSE streams to the dashboard
  memory          = "1Gi"
  env             = local.common_env
  depends_on      = [module.secrets, google_cloud_tasks_queue.this]
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
