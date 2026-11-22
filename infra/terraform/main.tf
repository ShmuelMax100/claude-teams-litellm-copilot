locals {
  tags                           = merge({ managed_by = "terraform", project = "litellm-copilot" }, var.tags)
  external_database_secret_scope = try("${var.external_database_key_vault_id}/secrets/${split("/", var.external_database_secret_id)[4]}", null)
  fusion_vault_name              = try(element(reverse(split("/", var.fusion_key_vault_id)), 0), "")
  fusion_secret_scopes = merge({
    service = "${var.fusion_key_vault_id}/secrets/${try(split("/", var.fusion_service_token_secret_id)[4], "missing")}"
    }, { for subject, user in var.fusion_users : "user-${substr(sha256(subject), 0, 16)}" =>
    "${var.fusion_key_vault_id}/secrets/${try(split("/", user.github_token_secret_id)[4], "missing")}"
  })
}

resource "terraform_data" "requirements" {
  lifecycle {
    precondition {
      condition     = length(distinct([for scope in values(local.fusion_secret_scopes) : lower(scope)])) == length(local.fusion_secret_scopes)
      error_message = "Each user must have a different PAT secret scope; the service bearer must be separate from every GitHub credential. Secret versions and casing do not establish isolation."
    }
    precondition {
      condition = alltrue([for user in values(var.fusion_users) :
        try(timecmp(timeadd("1970-01-01T00:00:00Z", "${user.expires_at}s"), plantimestamp()) > 0, false)
      ])
      error_message = "Every configured GitHub credential must have a future expiry. Expired credentials cannot authorize a deployment."
    }
    precondition {
      condition = var.enable_managed_postgresql || (
        can(regex("^https://[a-zA-Z0-9-]+\\.vault\\.azure\\.net/secrets/[^/]+(/[^/]+)?$", var.external_database_secret_id)) &&
        can(regex("^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft.KeyVault/vaults/[^/]+$", var.external_database_key_vault_id))
      )
      error_message = "External PostgreSQL requires a Key Vault secret URI and vault resource ID. The stored URL must require TLS."
    }
    precondition {
      condition = (
        can(regex("@sha256:[0-9a-f]{64}$", var.fusion_image)) &&
        can(regex("^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft.KeyVault/vaults/[a-zA-Z0-9-]+$", var.fusion_key_vault_id)) &&
        alltrue([for uri in concat([var.fusion_service_token_secret_id], [for user in values(var.fusion_users) : user.github_token_secret_id]) :
          can(regex("^https://[a-zA-Z0-9-]+\\.vault\\.azure\\.net/secrets/[a-zA-Z0-9-]+(/[a-zA-Z0-9-]+)?$", uri)) &&
          try(lower(split("/", uri)[2]) == "${lower(local.fusion_vault_name)}.vault.azure.net", false)
        ])
      )
      error_message = "Fusion is required: provide an immutable image and valid existing secret URIs in the declared Key Vault."
    }
    precondition {
      condition     = (var.registry_server == null) == (var.registry_resource_id == null)
      error_message = "Set both registry_server and registry_resource_id, or neither."
    }
    precondition {
      condition = (
        var.litellm_min_replicas >= 0 && var.litellm_max_replicas >= max(1, var.litellm_min_replicas) &&
        var.fusion_min_replicas >= 0 && var.fusion_max_replicas >= max(1, var.fusion_min_replicas)
      )
      error_message = "Replica bounds must be nonnegative, with max at least 1 and no smaller than min."
    }
  }
}

resource "random_string" "suffix" {
  length  = 5
  special = false
  upper   = false
}

resource "azurerm_resource_group" "this" {
  name     = "${var.name_prefix}-rg"
  location = var.location
  tags     = local.tags
}

resource "azurerm_virtual_network" "this" {
  name                = "${var.name_prefix}-vnet"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  address_space       = [var.vnet_address_space]
  tags                = local.tags
}

resource "azurerm_subnet" "apps" {
  name                 = "container-apps"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = [cidrsubnet(var.vnet_address_space, 7, 0)]
  delegation {
    name = "container-apps"
    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_subnet" "postgresql" {
  count                = var.enable_managed_postgresql ? 1 : 0
  name                 = "postgresql"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = [cidrsubnet(var.vnet_address_space, 7, 1)]
  service_endpoints    = ["Microsoft.Storage"]
  delegation {
    name = "postgresql"
    service_delegation {
      name    = "Microsoft.DBforPostgreSQL/flexibleServers"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

module "monitoring" {
  source              = "./modules/monitoring"
  name                = "${var.name_prefix}-logs"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  retention_days      = var.log_retention_days
  daily_quota_gb      = var.log_daily_quota_gb
  tags                = local.tags
}

module "postgresql" {
  count                 = var.enable_managed_postgresql ? 1 : 0
  source                = "./modules/postgresql"
  name                  = "${var.name_prefix}-pg-${random_string.suffix.result}"
  location              = azurerm_resource_group.this.location
  resource_group_name   = azurerm_resource_group.this.name
  subnet_id             = azurerm_subnet.postgresql[0].id
  virtual_network_id    = azurerm_virtual_network.this.id
  sku_name              = var.postgresql_sku
  storage_mb            = var.postgresql_storage_mb
  backup_retention_days = var.postgresql_backup_retention_days
  tags                  = local.tags
}

module "key_vault" {
  source                  = "./modules/key-vault"
  name                    = "${replace(var.name_prefix, "-", "")}kv${random_string.suffix.result}"
  location                = azurerm_resource_group.this.location
  resource_group_name     = azurerm_resource_group.this.name
  tenant_id               = data.azurerm_client_config.current.tenant_id
  administrator_object_id = data.azurerm_client_config.current.object_id
  create_database_secret  = var.enable_managed_postgresql
  database_url            = var.enable_managed_postgresql ? module.postgresql[0].database_url : null
  tags                    = local.tags
}

resource "azurerm_user_assigned_identity" "litellm" {
  name                = "${var.name_prefix}-litellm"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags
}

resource "azurerm_user_assigned_identity" "fusion" {
  name                = "${var.name_prefix}-fusion"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags
}

resource "azurerm_role_assignment" "litellm_secrets" {
  for_each             = toset(var.enable_managed_postgresql ? ["master-key", "salt-key", "database-url"] : ["master-key", "salt-key"])
  scope                = "${module.key_vault.id}/secrets/${each.key}"
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.litellm.principal_id
}

resource "azurerm_role_assignment" "external_database" {
  count                = !var.enable_managed_postgresql && local.external_database_secret_scope != null ? 1 : 0
  scope                = local.external_database_secret_scope
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.litellm.principal_id
}

resource "azurerm_role_assignment" "fusion_secrets" {
  for_each             = local.fusion_secret_scopes
  scope                = each.value
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.fusion.principal_id
}

resource "azurerm_role_assignment" "registry_litellm" {
  count                = var.registry_resource_id == null ? 0 : 1
  scope                = var.registry_resource_id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.litellm.principal_id
}

# The gateway guard calls the private service; it never receives the GitHub PAT.
resource "azurerm_role_assignment" "litellm_fusion_bearer" {
  scope                = local.fusion_secret_scopes["service"]
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.litellm.principal_id
}

resource "azurerm_role_assignment" "fusion_identity_key" {
  for_each             = { gateway = azurerm_user_assigned_identity.litellm.principal_id, fusion = azurerm_user_assigned_identity.fusion.principal_id }
  scope                = "${module.key_vault.id}/secrets/fusion-identity-key"
  role_definition_name = "Key Vault Secrets User"
  principal_id         = each.value
}

# Preserve existing optional deployment addresses when adopting mandatory Fusion.
moved {
  from = azurerm_user_assigned_identity.fusion[0]
  to   = azurerm_user_assigned_identity.fusion
}
moved {
  from = azurerm_role_assignment.litellm_fusion_bearer[0]
  to   = azurerm_role_assignment.litellm_fusion_bearer
}
moved {
  from = azurerm_role_assignment.fusion_secrets["mcp"]
  to   = azurerm_role_assignment.fusion_secrets["service"]
}

resource "azurerm_role_assignment" "registry_fusion" {
  count                = var.registry_resource_id != null ? 1 : 0
  scope                = var.registry_resource_id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.fusion.principal_id
}

module "container_apps" {
  source                         = "./modules/container-apps"
  name_prefix                    = var.name_prefix
  location                       = azurerm_resource_group.this.location
  resource_group_name            = azurerm_resource_group.this.name
  subnet_id                      = azurerm_subnet.apps.id
  log_analytics_workspace_id     = module.monitoring.id
  private_environment            = var.enable_private_networking
  litellm_identity_id            = azurerm_user_assigned_identity.litellm.id
  master_key_secret_id           = module.key_vault.master_key_secret_id
  salt_key_secret_id             = module.key_vault.salt_key_secret_id
  database_secret_id             = var.enable_managed_postgresql ? module.key_vault.database_secret_id : var.external_database_secret_id
  litellm_image                  = var.litellm_image
  litellm_cpu                    = var.litellm_cpu
  litellm_memory                 = var.litellm_memory
  litellm_min_replicas           = var.litellm_min_replicas
  litellm_max_replicas           = var.litellm_max_replicas
  litellm_http_concurrency       = var.litellm_http_concurrency
  litellm_log_level              = var.litellm_log_level
  gateway_allowed_cidrs          = var.gateway_allowed_cidrs
  fusion_guard_source            = file("${path.module}/../../services/litellm-fusion-gateway/fusion_gateway_guard.py")
  fusion_identity_id             = azurerm_user_assigned_identity.fusion.id
  fusion_image                   = var.fusion_image
  fusion_users                   = var.fusion_users
  fusion_service_token_secret_id = var.fusion_service_token_secret_id
  fusion_identity_key_secret_id  = module.key_vault.fusion_identity_key_secret_id
  fusion_cpu                     = var.fusion_cpu
  fusion_memory                  = var.fusion_memory
  fusion_min_replicas            = var.fusion_min_replicas
  fusion_max_replicas            = var.fusion_max_replicas
  fusion_timeout_seconds         = var.fusion_timeout_seconds
  fusion_max_concurrency         = var.fusion_max_concurrency
  registry_server                = var.registry_server
  tags                           = local.tags

  depends_on = [terraform_data.requirements, azurerm_role_assignment.litellm_secrets,
    azurerm_role_assignment.external_database, azurerm_role_assignment.fusion_secrets,
    azurerm_role_assignment.litellm_fusion_bearer, azurerm_role_assignment.fusion_identity_key,
  azurerm_role_assignment.registry_litellm, azurerm_role_assignment.registry_fusion]
}

# An internal ACA environment needs split DNS for its generated domain.
resource "azurerm_private_dns_zone" "apps" {
  count               = var.enable_private_networking ? 1 : 0
  name                = module.container_apps.default_domain
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "apps" {
  count                 = var.enable_private_networking ? 1 : 0
  name                  = "container-apps"
  resource_group_name   = azurerm_resource_group.this.name
  private_dns_zone_name = azurerm_private_dns_zone.apps[0].name
  virtual_network_id    = azurerm_virtual_network.this.id
  tags                  = local.tags
}

resource "azurerm_private_dns_a_record" "apps" {
  for_each            = var.enable_private_networking ? toset(["*", "@"]) : toset([])
  name                = each.key
  zone_name           = azurerm_private_dns_zone.apps[0].name
  resource_group_name = azurerm_resource_group.this.name
  ttl                 = 300
  records             = [module.container_apps.static_ip_address]
  tags                = local.tags
}
