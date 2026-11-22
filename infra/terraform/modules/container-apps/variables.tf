variable "name_prefix" {
  type = string
}

variable "location" {
  type = string
}

variable "resource_group_name" {
  type = string
}

variable "subnet_id" {
  type = string
}

variable "log_analytics_workspace_id" {
  type = string
}

variable "private_environment" {
  type = bool
}

variable "litellm_identity_id" {
  type = string
}

variable "master_key_secret_id" {
  type = string
}

variable "salt_key_secret_id" {
  type = string
}

variable "database_secret_id" {
  type = string
}

variable "litellm_image" {
  type = string
}

variable "litellm_cpu" {
  type = number
}

variable "litellm_memory" {
  type = string
}

variable "litellm_min_replicas" {
  type = number
}

variable "litellm_max_replicas" {
  type = number
}

variable "litellm_http_concurrency" {
  type = number
}

variable "litellm_log_level" {
  type = string
}

variable "gateway_allowed_cidrs" {
  type = list(string)
}

variable "fusion_guard_source" {
  description = "Checked-in nonsecret guard enforcing the only supported Fusion inference path."
  type        = string
}

variable "fusion_identity_id" {
  type = string
}

variable "fusion_image" {
  type = string
}

variable "fusion_cpu" {
  type = number
}

variable "fusion_memory" {
  type = string
}

variable "fusion_min_replicas" {
  type = number
}

variable "fusion_max_replicas" {
  type = number
}

variable "fusion_timeout_seconds" {
  type = number
}

variable "fusion_max_concurrency" {
  type = number
}

variable "registry_server" {
  type = string
}

variable "tags" {
  type = map(string)
}

variable "fusion_users" {
  type = map(object({
    github_token_secret_id = string
    expires_at             = number
  }))
}

variable "fusion_service_token_secret_id" { type = string }
variable "fusion_identity_key_secret_id" { type = string }
