variable "subscription_id" {
  description = "Subscription to deploy to; no default selects an account implicitly."
  type        = string
}

variable "name_prefix" {
  description = "Short lowercase environment prefix; generated suffixes make global resource names unique."
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,14}[a-z0-9]$", var.name_prefix))
    error_message = "Use 3–16 lowercase letters, digits or hyphens, starting with a letter and ending with a letter or digit."
  }
}

variable "location" {
  type    = string
  default = "westeurope"
}

variable "tags" {
  type = map(string)
  default = {
    environment = "dev"
    owner       = "unassigned"
  }
}

variable "enable_fusion_mcp" {
  type    = bool
  default = false
}

variable "enable_fusion_fallback" {
  description = "Explicitly enable the single-owner guarded native Fusion fallback adapter. Requires enable_fusion_mcp; model/key fallback enrollment remains a separate administrator action."
  type        = bool
  default     = false
}

variable "enable_managed_postgresql" {
  type    = bool
  default = true
}

variable "enable_private_networking" {
  description = "Make the ACA environment private; developers then need VPN/peering and DNS. Managed PostgreSQL and Fusion are always private. Changing this replaces the environment."
  type        = bool
  default     = false
}

variable "vnet_address_space" {
  type    = string
  default = "10.40.0.0/16"
  validation {
    condition     = can(cidrnetmask(var.vnet_address_space)) && try(tonumber(split("/", var.vnet_address_space)[1]) <= 20, false)
    error_message = "Provide a valid IPv4 CIDR with prefix /20 or larger; derived ACA subnet must be /27 or larger."
  }
}

variable "litellm_image" {
  description = "Official image validated by this repository; upgrade deliberately after compatibility tests."
  type        = string
  default     = "ghcr.io/berriai/litellm:v1.108.0@sha256:c560d317136880ea92d62334d4367ab10955b1af189dd3ea24f6551290b1d6c1"
  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.litellm_image))
    error_message = "Pin the LiteLLM image by sha256 digest."
  }
}

variable "litellm_cpu" {
  type    = number
  default = 1
}

variable "litellm_memory" {
  type    = string
  default = "2Gi"
}

variable "litellm_min_replicas" {
  type    = number
  default = 1
}

variable "litellm_max_replicas" {
  type    = number
  default = 2
}

variable "litellm_http_concurrency" {
  type    = number
  default = 20
}

variable "litellm_log_level" {
  description = "Gateway operational logging level; message logging remains disabled. DEBUG may expose sensitive details and is intentionally unavailable."
  type        = string
  default     = "WARNING"
  validation {
    condition     = contains(["INFO", "WARNING", "ERROR", "CRITICAL"], var.litellm_log_level)
    error_message = "Choose INFO, WARNING, ERROR or CRITICAL."
  }
}

variable "copilot_only_models" {
  description = "Optional explicit alias => entitled Copilot model ID map for existing copilot-only mode. Uses file-managed per-user OAuth routes without provider credentials. Empty by default."
  type        = map(string)
  default     = {}
  validation {
    condition = alltrue([for alias, model in var.copilot_only_models :
      can(regex("^[A-Za-z0-9][A-Za-z0-9_.-]*$", alias)) && can(regex("^[A-Za-z0-9][A-Za-z0-9_.-]*$", model))
    ])
    error_message = "Provide literal aliases and model IDs, with letters, digits, dots, underscores or hyphens; no URLs, secrets or wildcards."
  }
}

variable "gateway_allowed_cidrs" {
  description = "Optional developer ingress IP allowlist. An empty list relies on LiteLLM authentication."
  type        = list(string)
  default     = []
  validation {
    condition     = alltrue([for cidr in var.gateway_allowed_cidrs : can(cidrhost(cidr, 0))])
    error_message = "Ingress entries must be CIDR ranges."
  }
}

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "log_daily_quota_gb" {
  description = "Log Analytics ingestion cap; reaching it stops ingestion and reduces visibility."
  type        = number
  default     = 1
}

variable "postgresql_sku" {
  type    = string
  default = "B_Standard_B1ms"
}

variable "postgresql_storage_mb" {
  type    = number
  default = 32768
}

variable "postgresql_backup_retention_days" {
  type    = number
  default = 7
  validation {
    condition     = var.postgresql_backup_retention_days >= 7 && var.postgresql_backup_retention_days <= 35
    error_message = "Backup retention must be 7–35 days."
  }
}

variable "external_database_secret_id" {
  description = "Existing Key Vault secret URI containing a PostgreSQL URL with TLS enabled; required when managed PostgreSQL is disabled. Never put a connection string here."
  type        = string
  default     = null
}

variable "external_database_key_vault_id" {
  description = "Azure resource ID of the vault holding external_database_secret_id, used for secret-scoped RBAC."
  type        = string
  default     = null
}

variable "fusion_image" {
  description = "Operator-built Fusion image, pinned by digest. No image is built or published by Terraform."
  type        = string
  default     = null
}

variable "fusion_single_user_subject" {
  description = "Exact LiteLLM user ID of the one administrator-attested GitHub token owner. No multi-user execution."
  type        = string
  default     = null
}

variable "fusion_key_vault_id" {
  description = "Azure resource ID of the vault containing both Fusion secrets."
  type        = string
  default     = null
}

variable "fusion_github_token_secret_id" {
  description = "Existing Key Vault secret URI for the single owner's fine-grained PAT."
  type        = string
  default     = null
}

variable "fusion_mcp_token_secret_id" {
  description = "Existing Key Vault secret URI for an independent random service bearer (at least 32 characters, enforced by the service)."
  type        = string
  default     = null
}

variable "fusion_cpu" {
  type    = number
  default = 1
}

variable "fusion_memory" {
  type    = string
  default = "2Gi"
}

variable "fusion_min_replicas" {
  description = "Keep the SDK runtime warm when Fusion is explicitly enabled. One replica incurs ongoing idle/active charges; set 0 to permit cold starts and scale-to-zero. Fusion remains disabled by default."
  type        = number
  default     = 1
}

variable "fusion_max_replicas" {
  description = "Per-replica SDK runtimes are independent. The single-user deployment defaults to one replica."
  type        = number
  default     = 1
}

variable "fusion_timeout_seconds" {
  description = "SDK tool execution deadline. At most 210 seconds leaves transport/cleanup margin below ACA's 240-second HTTP ingress request timeout."
  type        = number
  default     = 120
  validation {
    condition     = var.fusion_timeout_seconds >= 1 && var.fusion_timeout_seconds <= 210 && floor(var.fusion_timeout_seconds) == var.fusion_timeout_seconds
    error_message = "Fusion execution timeout must be an integer from 1 through 210 seconds for ACA HTTP ingress."
  }
}

variable "fusion_primary_timeout_seconds" {
  description = "Anthropic primary attempt deadline when guarded fallback is enabled. Primary + Fusion execution + 20 seconds of fallback transport must fit within 230 seconds, leaving ACA ingress overhead."
  type        = number
  default     = 60
  validation {
    condition     = var.fusion_primary_timeout_seconds >= 1 && var.fusion_primary_timeout_seconds <= 180 && floor(var.fusion_primary_timeout_seconds) == var.fusion_primary_timeout_seconds
    error_message = "Fusion fallback primary timeout must be an integer from 1 through 180 seconds."
  }
}

variable "fusion_max_concurrency" {
  description = "Maximum concurrent isolated SDK sessions per replica, also used as the HTTP autoscaling target. This does not authorize additional users."
  type        = number
  default     = 2
  validation {
    condition     = var.fusion_max_concurrency >= 1 && var.fusion_max_concurrency <= 16 && floor(var.fusion_max_concurrency) == var.fusion_max_concurrency
    error_message = "Fusion concurrency must be an integer from 1 through 16."
  }
}

variable "registry_server" {
  description = "Optional existing ACR hostname for private image pulls."
  type        = string
  default     = null
}

variable "registry_resource_id" {
  description = "Resource ID of registry_server, granting AcrPull to app identities."
  type        = string
  default     = null
}
