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
    condition     = !module.container_apps.contract.fusion_enabled && module.container_apps.contract.fusion_runtime == null && output.fusion_mcp_url == null && length(azurerm_user_assigned_identity.fusion) == 0
    error_message = "Fusion must have no deployment or identity by default."
  }
  assert {
    condition     = module.container_apps.contract.gateway_external_ingress && !module.container_apps.contract.gateway_allow_insecure
    error_message = "Default gateway must use external HTTPS ingress."
  }
  assert {
    condition = (
      !module.container_apps.contract.fusion_fallback_enabled &&
      !can(jsondecode(module.container_apps.contract.config).litellm_settings.callbacks) &&
      length(azurerm_role_assignment.litellm_fusion_bearer) == 0 &&
      length(module.container_apps.contract.gateway_fallback.settings) == 0 &&
      length(module.container_apps.contract.gateway_fallback.bearer_secret) == 0 &&
      alltrue([for mount in module.container_apps.contract.gateway_fallback.volume_mounts : mount.name != "fusion-guard"])
    )
    error_message = "Default gateway must not load a fallback guard, mount its source, add fallback settings, or gain bearer-secret access."
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
  assert {
    condition = (
      !module.container_apps.contract.fusion_fallback_enabled &&
      !can(module.container_apps.contract.fusion_runtime.settings["FUSION_ENABLE_FALLBACK"]) &&
      length(azurerm_role_assignment.litellm_fusion_bearer) == 0
    )
    error_message = "Enabling MCP alone must not enable native fallback or authorize the gateway's bearer access."
  }
  assert {
    condition = (
      module.container_apps.contract.fusion_runtime.min_replicas == 1 &&
      module.container_apps.contract.fusion_runtime.max_replicas == 1 &&
      module.container_apps.contract.fusion_runtime.settings["FUSION_TIMEOUT_SECONDS"] == "120" &&
      module.container_apps.contract.fusion_runtime.settings["FUSION_MAX_CONCURRENCY"] == "2" &&
      module.container_apps.contract.fusion_runtime.http_concurrency == "2" &&
      module.container_apps.contract.fusion_runtime.termination_grace_seconds == 150
    )
    error_message = "Enabled Fusion must keep one warm runtime, bound sessions, and allow orderly shutdown by default."
  }
  assert {
    condition = (
      module.container_apps.contract.fusion_runtime.target_port == 8080 &&
      module.container_apps.contract.fusion_runtime.startup_probe.path == "/health/live" &&
      module.container_apps.contract.fusion_runtime.startup_probe.interval_seconds * module.container_apps.contract.fusion_runtime.startup_probe.failure_count_threshold >= 300 &&
      module.container_apps.contract.fusion_runtime.liveness_probe.path == "/health/live" &&
      module.container_apps.contract.fusion_runtime.readiness_probe.path == "/health/ready" &&
      module.container_apps.contract.fusion_runtime.readiness_probe.timeout == 5
    )
    error_message = "Expose only MCP port 8080; use non-inference liveness and SDK-aware readiness with startup grace."
  }
}

run "fallback_loads_guard_and_grants_only_private_service_bearer" {
  command = plan
  variables {
    enable_fusion_mcp             = true
    enable_fusion_fallback        = true
    fusion_image                  = "registry.example/fusion@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    fusion_single_user_subject    = "test-owner"
    fusion_key_vault_id           = "/subscriptions/00000000-0000-0000-0000-000000000003/resourceGroups/secrets/providers/Microsoft.KeyVault/vaults/test-vault"
    fusion_github_token_secret_id = "https://test-vault.vault.azure.net/secrets/github-token"
    fusion_mcp_token_secret_id    = "https://test-vault.vault.azure.net/secrets/mcp-token"
  }
  assert {
    condition = (
      module.container_apps.contract.fusion_fallback_enabled &&
      tolist(jsondecode(module.container_apps.contract.config).litellm_settings.callbacks) == tolist(["fusion_fallback_guard.guard"]) &&
      module.container_apps.contract.fusion_runtime.settings["FUSION_ENABLE_FALLBACK"] == "true" &&
      !module.container_apps.contract.fusion_external_ingress &&
      module.container_apps.contract.fusion_runtime.target_port == 8080
    )
    error_message = "Explicit fallback must load the guard and enable the adapter only on the existing private Fusion service."
  }
  assert {
    condition = (
      module.container_apps.contract.gateway_fallback.settings["PYTHONPATH"] == "/app" &&
      module.container_apps.contract.gateway_fallback.settings["FUSION_FALLBACK_OWNER_SUBJECT"] == "test-owner" &&
      contains(keys(module.container_apps.contract.gateway_fallback.settings), "FUSION_FALLBACK_API_BASE") &&
      module.container_apps.contract.gateway_fallback.settings["FUSION_FALLBACK_PRIMARY_TIMEOUT_SECONDS"] == "60" &&
      module.container_apps.contract.gateway_fallback.settings["FUSION_FALLBACK_TIMEOUT_SECONDS"] == "140" &&
      module.container_apps.contract.gateway_fallback.settings["FUSION_GUARD_SHA256"] == filesha256("../../services/litellm-fusion-guard/fusion_fallback_guard.py") &&
      length([for mount in module.container_apps.contract.gateway_fallback.volume_mounts : mount
        if mount.name == "fusion-guard" && mount.path == "/app/fusion_fallback_guard.py" && mount.sub_path == "fusion-guard"
      ]) == 1
    )
    error_message = "The source-backed guard must be importable, owner-bound, and trigger a new revision when its content changes."
  }
  assert {
    condition = (
      tolist(module.container_apps.contract.gateway_fallback.bearer_secret) == tolist(["fusion-mcp-token"]) &&
      toset(module.container_apps.contract.gateway_fallback.secret_names) == toset(["database-url", "master-key", "salt-key", "fusion-mcp-token"]) &&
      length(azurerm_role_assignment.litellm_fusion_bearer) == 1 &&
      azurerm_role_assignment.litellm_fusion_bearer[0].scope == "/subscriptions/00000000-0000-0000-0000-000000000003/resourceGroups/secrets/providers/Microsoft.KeyVault/vaults/test-vault/secrets/mcp-token" &&
      azurerm_role_assignment.litellm_fusion_bearer[0].role_definition_name == "Key Vault Secrets User"
    )
    error_message = "The gateway may read only its own secrets plus the private service bearer, never Fusion's GitHub PAT."
  }
}

run "fallback_without_fusion_is_rejected" {
  command = plan
  variables { enable_fusion_fallback = true }
  expect_failures = [terraform_data.requirements]
}

run "fallback_exceeding_combined_ingress_budget_is_rejected" {
  command = plan
  variables {
    enable_fusion_mcp              = true
    enable_fusion_fallback         = true
    fusion_image                   = "registry.example/fusion@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    fusion_single_user_subject     = "test-owner"
    fusion_key_vault_id            = "/subscriptions/00000000-0000-0000-0000-000000000003/resourceGroups/secrets/providers/Microsoft.KeyVault/vaults/test-vault"
    fusion_github_token_secret_id  = "https://test-vault.vault.azure.net/secrets/github-token"
    fusion_mcp_token_secret_id     = "https://test-vault.vault.azure.net/secrets/mcp-token"
    fusion_timeout_seconds         = 210
    fusion_primary_timeout_seconds = 1
  }
  expect_failures = [terraform_data.requirements]
}

run "excessive_primary_timeout_is_rejected" {
  command = plan
  variables { fusion_primary_timeout_seconds = 181 }
  expect_failures = [var.fusion_primary_timeout_seconds]
}

run "fallback_with_reused_github_secret_is_rejected" {
  command = plan
  variables {
    enable_fusion_mcp             = true
    enable_fusion_fallback        = true
    fusion_image                  = "registry.example/fusion@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    fusion_single_user_subject    = "test-owner"
    fusion_key_vault_id           = "/subscriptions/00000000-0000-0000-0000-000000000003/resourceGroups/secrets/providers/Microsoft.KeyVault/vaults/test-vault"
    fusion_github_token_secret_id = "https://test-vault.vault.azure.net/secrets/github-token"
    fusion_mcp_token_secret_id    = "https://test-vault.vault.azure.net/secrets/GitHub-Token/00000000000000000000000000000001"
  }
  expect_failures = [terraform_data.requirements]
}

run "fusion_can_explicitly_scale_to_zero_and_tune_sessions" {
  command = plan
  variables {
    enable_fusion_mcp             = true
    fusion_image                  = "registry.example/fusion@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    fusion_single_user_subject    = "test-owner"
    fusion_key_vault_id           = "/subscriptions/00000000-0000-0000-0000-000000000003/resourceGroups/secrets/providers/Microsoft.KeyVault/vaults/test-vault"
    fusion_github_token_secret_id = "https://test-vault.vault.azure.net/secrets/github-token"
    fusion_mcp_token_secret_id    = "https://test-vault.vault.azure.net/secrets/mcp-token"
    fusion_min_replicas           = 0
    fusion_timeout_seconds        = 210
    fusion_max_concurrency        = 4
  }
  assert {
    condition = (
      module.container_apps.contract.fusion_runtime.min_replicas == 0 &&
      module.container_apps.contract.fusion_runtime.max_replicas == 1 &&
      module.container_apps.contract.fusion_runtime.settings["FUSION_TIMEOUT_SECONDS"] == "210" &&
      module.container_apps.contract.fusion_runtime.settings["FUSION_MAX_CONCURRENCY"] == "4" &&
      module.container_apps.contract.fusion_runtime.http_concurrency == "4" &&
      module.container_apps.contract.fusion_runtime.termination_grace_seconds == 240
    )
    error_message = "The explicit cost-saving override must scale to zero while matching runtime and scale-rule concurrency."
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

run "timeout_without_aca_ingress_margin_is_rejected" {
  command = plan
  variables { fusion_timeout_seconds = 240 }
  expect_failures = [var.fusion_timeout_seconds]
}

run "unbounded_session_concurrency_is_rejected" {
  command = plan
  variables { fusion_max_concurrency = 0 }
  expect_failures = [var.fusion_max_concurrency]
}
