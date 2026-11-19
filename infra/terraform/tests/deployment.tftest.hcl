# Native provider mocks: no Azure login, deployment, or paid provider calls.
mock_provider "azurerm" {
  mock_data "azurerm_client_config" {
    defaults = {
      tenant_id       = "00000000-0000-0000-0000-000000000001"
      object_id       = "00000000-0000-0000-0000-000000000002"
      subscription_id = "00000000-0000-0000-0000-000000000003"
    }
  }
}

mock_provider "random" {}

variables {
  subscription_id = "00000000-0000-0000-0000-000000000003"
  name_prefix     = "test-gateway"
}

run "default_gateway_is_persistent_and_fusion_is_absent" {
  command = plan
  assert {
    condition     = length(module.postgresql) == 1 && length(azurerm_subnet.postgresql) == 1
    error_message = "Default deployment must provision persistent PostgreSQL in its own subnet."
  }
  assert {
    condition     = !module.container_apps.contract.fusion_enabled && output.fusion_mcp_url == null && length(azurerm_user_assigned_identity.fusion) == 0
    error_message = "Fusion must have no deployment or identity by default."
  }
  assert {
    condition     = module.container_apps.contract.gateway_external_ingress && !module.container_apps.contract.gateway_allow_insecure
    error_message = "Default gateway must use external HTTPS ingress."
  }
  assert {
    condition = (
      jsondecode(module.container_apps.contract.config).general_settings.store_model_in_db &&
      jsondecode(module.container_apps.contract.config).general_settings.database_url == "os.environ/DATABASE_URL" &&
      jsondecode(module.container_apps.contract.config).general_settings.master_key == "os.environ/LITELLM_MASTER_KEY" &&
      length(azurerm_role_assignment.litellm_secrets) == 3
    )
    error_message = "Gateway must use database model storage and secret references, with scoped grants."
  }
}

run "fusion_is_private_single_owner_and_separately_authorized" {
  command = plan
  variables {
    enable_fusion_mcp             = true
    fusion_image                  = "registry.example/fusion@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    fusion_single_user_subject    = "test-owner"
    fusion_key_vault_id           = "/subscriptions/00000000-0000-0000-0000-000000000003/resourceGroups/secrets/providers/Microsoft.KeyVault/vaults/test-vault"
    fusion_github_token_secret_id = "https://test-vault.vault.azure.net/secrets/github-token"
    fusion_mcp_token_secret_id    = "https://test-vault.vault.azure.net/secrets/mcp-token"
  }
  assert {
    condition     = module.container_apps.contract.fusion_enabled && !module.container_apps.contract.fusion_external_ingress
    error_message = "Fusion must be environment-only even when enabled."
  }
  assert {
    condition     = length(azurerm_user_assigned_identity.fusion) == 1 && length(azurerm_role_assignment.fusion_secrets) == 2
    error_message = "Fusion must have a separate identity and exactly its two secret grants."
  }
  assert {
    condition     = alltrue([for role in azurerm_role_assignment.fusion_secrets : strcontains(role.scope, "/secrets/")])
    error_message = "Fusion must never receive vault-wide read access."
  }
}

run "existing_database_uses_secret_reference_without_managed_server" {
  command = plan
  variables {
    enable_managed_postgresql      = false
    external_database_secret_id    = "https://test-vault.vault.azure.net/secrets/postgresql-url"
    external_database_key_vault_id = "/subscriptions/00000000-0000-0000-0000-000000000003/resourceGroups/secrets/providers/Microsoft.KeyVault/vaults/test-vault"
  }
  assert {
    condition     = length(module.postgresql) == 0 && length(azurerm_subnet.postgresql) == 0 && output.postgresql_server_id == null
    error_message = "Existing database mode must not create a server or database subnet."
  }
  assert {
    condition     = length(azurerm_role_assignment.external_database) == 1 && endswith(azurerm_role_assignment.external_database[0].scope, "/secrets/postgresql-url")
    error_message = "External database access must be scoped to its existing Key Vault secret."
  }
}

run "private_environment_creates_split_dns" {
  command = plan
  variables { enable_private_networking = true }
  assert {
    condition     = module.container_apps.contract.private_environment && length(azurerm_private_dns_zone.apps) == 1 && length(azurerm_private_dns_a_record.apps) == 2
    error_message = "Private environments require private DNS for wildcard and apex names."
  }
}

run "missing_external_database_is_rejected" {
  command = plan
  variables { enable_managed_postgresql = false }
  expect_failures = [terraform_data.requirements]
}

run "unconfigured_fusion_is_rejected" {
  command = plan
  variables { enable_fusion_mcp = true }
  expect_failures = [terraform_data.requirements]
}

run "copilot_only_routes_preserve_personal_identity" {
  command = plan
  variables { copilot_only_models = { "claude-direct" = "claude-sonnet-5.5" } }
  assert {
    condition = (
      jsondecode(output.gateway_config_json).model_list[0].litellm_params.github_copilot_auth_type == "per_user_oauth" &&
      jsondecode(output.gateway_config_json).model_list[0].litellm_params.litellm_credential_name == "github-copilot-user" &&
      !jsondecode(output.gateway_config_json).general_settings.disable_spend_logs &&
      jsondecode(output.gateway_config_json).litellm_settings.turn_off_message_logging
    )
    error_message = "Explicit copilot-only routes must preserve per-user credentials and metadata audit without message logging."
  }
}

run "too_small_network_is_rejected" {
  command = plan
  variables { vnet_address_space = "10.40.0.0/24" }
  expect_failures = [var.vnet_address_space]
}
