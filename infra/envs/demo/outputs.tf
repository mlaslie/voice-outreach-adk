output "service_accounts" {
  value = module.sa.emails
}

output "recordings_bucket" {
  value = module.recordings.name
}

output "prompts_bucket" {
  value = module.prompts.name
}

output "image_repository" {
  value = module.registry.url
}

output "secrets" {
  description = "Created empty; add values with scripts/set-secrets.sh"
  value       = module.secrets.ids
}
