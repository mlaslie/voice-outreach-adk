# Analytics (docs/ANALYTICS-PLAN.md, phase 1).
#   export job:  Firestore calls -> de-identified rows -> BigQuery (full refresh)
#   service:     private; the nurse UI's BFF reads the summary from it
# One identity for both. It can read Firestore and its own dataset, nothing else.

locals {
  analytics    = module.sa.members["${local.p}-analytics"]
  analytics_on = var.analytics_image != ""
}

resource "google_bigquery_dataset" "analytics" {
  project                    = var.project_id
  dataset_id                 = replace("${local.p}_analytics", "-", "_")
  location                   = var.region
  description                = "De-identified call metrics. No names, numbers, dates of birth or free text."
  delete_contents_on_destroy = false
  depends_on                 = [module.services]
}

resource "google_bigquery_dataset_iam_member" "analytics_editor" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.analytics.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = local.analytics
}

# The keyed hash for patient_key. The value is added outside Terraform:
#   openssl rand -hex 32 | gcloud secrets versions add analytics-key --data-file=-
resource "google_secret_manager_secret" "analytics_key" {
  project   = var.project_id
  secret_id = "analytics-key"
  replication {
    auto {}
  }
  depends_on = [module.services]
}

resource "google_secret_manager_secret_iam_member" "analytics_key" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.analytics_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = local.analytics
}

locals {
  analytics_env = merge(local.common_env, {
    ANALYTICS_DATASET  = google_bigquery_dataset.analytics.dataset_id
    ANALYTICS_HMAC_KEY = "secret://analytics-key"
  })
}

resource "google_cloud_run_v2_job" "analytics_export" {
  count               = local.analytics_on ? 1 : 0
  project             = var.project_id
  location            = var.region
  name                = "${local.p}-analytics-export"
  deletion_protection = false
  template {
    task_count = 1
    template {
      service_account = module.sa.emails["${local.p}-analytics"]
      timeout         = "900s"
      max_retries     = 1
      containers {
        image   = var.analytics_image
        command = ["/app/.venv/bin/python", "-m", "voa_analytics.export"]
        resources {
          limits = { cpu = "1", memory = "1Gi" }
        }
        dynamic "env" {
          for_each = local.analytics_env
          content {
            name  = env.key
            value = env.value
          }
        }
      }
    }
  }
  depends_on = [google_secret_manager_secret_iam_member.analytics_key]
}

# Scheduler runs the job as the tasks-invoker identity.
resource "google_cloud_run_v2_job_iam_member" "export_runner" {
  count    = local.analytics_on ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.analytics_export[0].name
  role     = "roles/run.invoker"
  member   = module.sa.members["${local.p}-tasks-invoker"]
}

resource "google_cloud_scheduler_job" "analytics_export" {
  count            = local.analytics_on ? 1 : 0
  project          = var.project_id
  region           = var.region
  name             = "${local.p}-analytics-export"
  description      = "Refresh the analytics tables from Firestore"
  schedule         = var.analytics_export_schedule
  time_zone        = var.clinic_timezone
  attempt_deadline = "60s"
  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.analytics_export[0].name}:run"
    oauth_token {
      service_account_email = module.sa.emails["${local.p}-tasks-invoker"]
    }
  }
  depends_on = [google_cloud_run_v2_job_iam_member.export_runner]
}

module "analytics" {
  count           = local.analytics_on ? 1 : 0
  source          = "../../modules/cloud_run_service"
  project_id      = var.project_id
  region          = var.region
  name            = "${local.p}-analytics"
  image           = var.analytics_image
  service_account = module.sa.emails["${local.p}-analytics"]
  public          = false
  invokers        = [module.sa.members["${local.p}-ui"]]
  min_instances   = var.analytics_min_instances
  max_instances   = 2
  concurrency     = 40
  timeout_s       = 60
  memory          = "512Mi"
  env             = local.analytics_env
  depends_on      = [google_secret_manager_secret_iam_member.analytics_key]
}

output "analytics_url" {
  value = local.analytics_on ? module.analytics[0].url : null
}
