# =============================================================================
# Project Scolecite — Terraform Infrastructure (Optional)
# Usage: cd deploy/terraform && terraform init && terraform apply
# =============================================================================

terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

# ---- Variables ----
variable "project_id" {
  description = "GCP Project ID"
  type        = string
}

variable "region" {
  description = "GCP Region"
  type        = string
  default     = "asia-northeast3"
}

variable "service_name" {
  description = "Cloud Run service name"
  type        = string
  default     = "scolecite-bot"
}

variable "db_password" {
  description = "Cloud SQL database password"
  type        = string
  sensitive   = true
}

# ---- Provider ----
provider "google" {
  project = var.project_id
  region  = var.region
}

# ---- Enable APIs ----
resource "google_project_service" "apis" {
  for_each = toset([
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "sqladmin.googleapis.com",
    "secretmanager.googleapis.com",
    "vpcaccess.googleapis.com",
    "compute.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

# ---- Artifact Registry ----
resource "google_artifact_registry_repository" "scolecite" {
  location      = var.region
  repository_id = "scolecite"
  format        = "DOCKER"
  description   = "Project Scolecite container images"
  depends_on    = [google_project_service.apis]
}

# ---- Cloud SQL (PostgreSQL 15) ----
resource "google_sql_database_instance" "main" {
  name             = "scolecite-db"
  database_version = "POSTGRES_15"
  region           = var.region

  settings {
    tier              = "db-f1-micro"
    availability_type = "ZONAL"
    disk_size         = 10
    disk_autoresize   = true

    backup_configuration {
      enabled    = true
      start_time = "04:00"
    }

    maintenance_window {
      day  = 7 # Sunday
      hour = 3
    }

    ip_configuration {
      ipv4_enabled    = false
      private_network = "projects/${var.project_id}/global/networks/default"
    }
  }

  deletion_protection = true
  depends_on          = [google_project_service.apis]
}

resource "google_sql_database" "scolecite" {
  name     = "scolecite"
  instance = google_sql_database_instance.main.name
}

resource "google_sql_user" "scolecite" {
  name     = "scolecite"
  instance = google_sql_database_instance.main.name
  password = var.db_password
}

# ---- VPC Connector ----
resource "google_vpc_access_connector" "connector" {
  name          = "scolecite-vpc"
  region        = var.region
  network       = "default"
  ip_cidr_range = "10.8.0.0/28"
  min_instances = 2
  max_instances = 3
  machine_type  = "e2-micro"
  depends_on    = [google_project_service.apis]
}

# ---- Secret Manager ----
locals {
  secret_names = [
    "ANTHROPIC_API_KEY",
    "XAI_GROK_API_KEY",
    "POLYGON_API_KEY",
    "APCA_API_KEY_ID",
    "APCA_API_SECRET_KEY",
    "DATABASE_URL",
  ]
}

resource "google_secret_manager_secret" "secrets" {
  for_each  = toset(local.secret_names)
  secret_id = each.value

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

# Store DATABASE_URL secret value
resource "google_secret_manager_secret_version" "db_url" {
  secret      = google_secret_manager_secret.secrets["DATABASE_URL"].id
  secret_data = "postgresql+asyncpg://scolecite:${var.db_password}@/scolecite?host=/cloudsql/${google_sql_database_instance.main.connection_name}"
}

# IAM: Cloud Run SA can read secrets
data "google_project" "current" {}

resource "google_secret_manager_secret_iam_member" "access" {
  for_each  = toset(local.secret_names)
  secret_id = google_secret_manager_secret.secrets[each.value].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${data.google_project.current.number}-compute@developer.gserviceaccount.com"
}

# IAM: Cloud Run SA can access Cloud SQL
resource "google_project_iam_member" "cloudsql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${data.google_project.current.number}-compute@developer.gserviceaccount.com"
}

# ---- Cloud Run Service ----
resource "google_cloud_run_v2_service" "scolecite" {
  name     = var.service_name
  location = var.region

  template {
    scaling {
      min_instance_count = 0
      max_instance_count = 3
    }

    vpc_access {
      connector = google_vpc_access_connector.connector.id
      egress    = "ALL_TRAFFIC"
    }

    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/scolecite/${var.service_name}:latest"

      ports {
        container_port = 8000
      }

      resources {
        limits = {
          cpu    = "2"
          memory = "2Gi"
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      # Environment variables
      env {
        name  = "TRADING_MODE"
        value = "paper"
      }
      env {
        name  = "ENABLE_PROMPT_CACHING"
        value = "true"
      }

      # Secrets from Secret Manager
      dynamic "env" {
        for_each = toset(local.secret_names)
        content {
          name = env.value
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.secrets[env.value].secret_id
              version = "latest"
            }
          }
        }
      }

      # Cloud SQL volume mount
      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      # Startup probe
      startup_probe {
        http_get {
          path = "/health"
          port = 8000
        }
        initial_delay_seconds = 5
        period_seconds        = 10
        failure_threshold     = 3
        timeout_seconds       = 5
      }

      # Liveness probe
      liveness_probe {
        http_get {
          path = "/health"
          port = 8000
        }
        period_seconds  = 30
        timeout_seconds = 5
      }
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.main.connection_name]
      }
    }

    max_instance_request_concurrency = 80
    timeout                          = "300s"
    execution_environment            = "EXECUTION_ENVIRONMENT_GEN2"
  }

  depends_on = [
    google_secret_manager_secret_version.db_url,
    google_vpc_access_connector.connector,
  ]
}

# Allow unauthenticated access
resource "google_cloud_run_v2_service_iam_member" "public" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.scolecite.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# ---- Outputs ----
output "service_url" {
  value = google_cloud_run_v2_service.scolecite.uri
}

output "cloud_sql_connection" {
  value = google_sql_database_instance.main.connection_name
}
