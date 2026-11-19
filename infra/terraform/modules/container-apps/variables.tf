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

variable "copilot_only_models" {
  type = map(string)
}

variable "gateway_allowed_cidrs" {
  type = list(string)
}

variable "enable_fusion" {
  type = bool
}

variable "fusion_identity_id" {
  type = string
}

variable "fusion_image" {
  type = string
}

variable "fusion_single_user_subject" {
  type = string
}

variable "fusion_github_token_secret_id" {
  type = string
}

variable "fusion_mcp_token_secret_id" {
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

variable "registry_server" {
  type = string
}

variable "tags" {
  type = map(string)
}
