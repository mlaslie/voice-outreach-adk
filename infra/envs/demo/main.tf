locals {
  p = var.name_prefix

  services = [
    "aiplatform.googleapis.com",
    "discoveryengine.googleapis.com",
    "artifactregistry.googleapis.com",
    "bigquery.googleapis.com",
    "billingbudgets.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "cloudscheduler.googleapis.com",
    "cloudtasks.googleapis.com",
    "cloudtrace.googleapis.com",
    "firestore.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "iap.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "serviceusage.googleapis.com",
    "storage.googleapis.com",
    "texttospeech.googleapis.com",
  ]

  common_runtime_roles = [
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/cloudtrace.agent",
  ]
}

module "services" {
  source     = "../../modules/project_services"
  project_id = var.project_id
  services   = toset(local.services)
}

# --------------------------------------------------------------------------- identities

module "sa" {
  source     = "../../modules/service_accounts"
  project_id = var.project_id
  accounts = {
    "${local.p}-gateway" = {
      display_name  = "Voice gateway (telephony media <-> Gemini Live)"
      project_roles = concat(local.common_runtime_roles, ["roles/aiplatform.user", "roles/datastore.user"])
    }
    "${local.p}-api" = {
      display_name  = "Outreach API (REST, webhooks, tasks, SMS agent)"
      project_roles = concat(local.common_runtime_roles, ["roles/aiplatform.user", "roles/datastore.user"])
    }
    "${local.p}-ui" = {
      display_name  = "Nurse dashboard (Next.js BFF)"
      project_roles = local.common_runtime_roles
    }
    "${local.p}-nurse" = {
      display_name  = "Nurse assistant (Gemini Enterprise A2A agent)"
      project_roles = concat(local.common_runtime_roles, ["roles/aiplatform.user", "roles/datastore.user"])
    }
    "${local.p}-tasks-invoker" = {
      display_name  = "OIDC identity for Cloud Tasks and Scheduler"
      project_roles = []
    }
    "${local.p}-build" = {
      display_name  = "Cloud Build: build images and deploy Cloud Run"
      project_roles = ["roles/logging.logWriter", "roles/run.developer"]
    }
  }
  depends_on = [module.services]
}

locals {
  gateway = module.sa.members["${local.p}-gateway"]
  api     = module.sa.members["${local.p}-api"]
  build   = module.sa.members["${local.p}-build"]
}

# The build SA deploys services that run as the runtime SAs, so it must be able to act as them.
resource "google_service_account_iam_member" "build_acts_as_runtime" {
  for_each           = toset(["${local.p}-gateway", "${local.p}-api", "${local.p}-ui", "${local.p}-nurse"])
  service_account_id = "projects/${var.project_id}/serviceAccounts/${module.sa.emails[each.value]}"
  role               = "roles/iam.serviceAccountUser"
  member             = local.build
}

# The API mints OIDC tokens as the tasks-invoker SA when it enqueues Cloud Tasks.
resource "google_service_account_iam_member" "api_acts_as_invoker" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${module.sa.emails["${local.p}-tasks-invoker"]}"
  role               = "roles/iam.serviceAccountUser"
  member             = local.api
}

# --------------------------------------------------------------------------- data

module "firestore" {
  source            = "../../modules/firestore"
  project_id        = var.project_id
  location_id       = var.firestore_location
  delete_protection = var.protect_data
  depends_on        = [module.services]
}

module "recordings" {
  source            = "../../modules/storage"
  project_id        = var.project_id
  location          = var.region
  name              = "${var.project_id}-${local.p}-recordings"
  delete_after_days = var.recordings_retention_days
  force_destroy     = !var.protect_data
  iam = {
    "roles/storage.objectCreator" = [local.gateway]
    "roles/storage.objectAdmin"   = [local.api] # serve and delete (batch delete)
  }
  depends_on = [module.services]
}

module "prompts" {
  source        = "../../modules/storage"
  project_id    = var.project_id
  location      = var.region
  name          = "${var.project_id}-${local.p}-prompts"
  force_destroy = true # rendered TTS clips; reproducible from config
  iam = {
    "roles/storage.objectViewer"  = [local.gateway]
    "roles/storage.objectCreator" = [local.gateway] # caches rendered clips
    "roles/storage.objectAdmin"   = [local.api]
  }
  depends_on = [module.services]
}

module "secrets" {
  source     = "../../modules/secrets"
  project_id = var.project_id
  secrets = {
    "twilio-account-sid"    = [local.gateway, local.api]
    "twilio-api-key-sid"    = [local.gateway, local.api]
    "twilio-api-key-secret" = [local.gateway, local.api]
    "twilio-auth-token"     = [local.gateway, local.api] # webhook signature validation only
    "softphone-token-key"   = [local.gateway, local.api] # HMAC key for softphone test-call tokens
  }
  depends_on = [module.services]
}

module "registry" {
  source        = "../../modules/artifact_registry"
  project_id    = var.project_id
  location      = var.region
  repository_id = "${local.p}-images"
  writers       = [local.build]
  readers       = [local.gateway, local.api, module.sa.members["${local.p}-ui"]]
  depends_on    = [module.services]
}

# --------------------------------------------------------------------------- audit + cost

resource "google_project_iam_audit_config" "data_access" {
  for_each = var.enable_data_access_audit_logs ? toset([
    "datastore.googleapis.com",
    "storage.googleapis.com",
    "secretmanager.googleapis.com",
  ]) : toset([])
  project = var.project_id
  service = each.value
  audit_log_config {
    log_type = "DATA_READ"
  }
  audit_log_config {
    log_type = "DATA_WRITE"
  }
}

data "google_project" "this" {
  project_id = var.project_id
}

resource "google_billing_budget" "monthly" {
  count           = var.billing_account_id == "" ? 0 : 1
  billing_account = var.billing_account_id
  display_name    = "${local.p} monthly budget"
  budget_filter {
    projects = ["projects/${data.google_project.this.number}"]
  }
  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(var.monthly_budget_usd)
    }
  }
  threshold_rules {
    threshold_percent = 0.5
  }
  threshold_rules {
    threshold_percent = 0.9
  }
  threshold_rules {
    threshold_percent = 1.0
  }
  depends_on = [module.services]
}
