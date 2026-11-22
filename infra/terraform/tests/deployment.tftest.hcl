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
  subscription_id                = "00000000-0000-0000-0000-000000000003"
  name_prefix                    = "test-gateway"
  fusion_image                   = "registry.example/fusion@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  fusion_key_vault_id            = "/subscriptions/00000000-0000-0000-0000-000000000003/resourceGroups/secrets/providers/Microsoft.KeyVault/vaults/test-vault"
  fusion_service_token_secret_id = "https://test-vault.vault.azure.net/secrets/service-token"
  fusion_users = {
    user-a = { github_token_secret_id = "https://test-vault.vault.azure.net/secrets/user-a-token", expires_at = 4102444800 }
    user-b = { github_token_secret_id = "https://test-vault.vault.azure.net/secrets/user-b-token", expires_at = 4102444800 }
  }
}

run "mandatory_fusion_with_only_gateway_ingress" {
  command = plan
  assert {
    condition     = length(module.postgresql) == 1 && length(azurerm_subnet.postgresql) == 1
    error_message = "Default deployment must provision persistent PostgreSQL in its own subnet."
  }
  assert {
    condition     = !module.container_apps.contract.fusion_external_ingress && module.container_apps.contract.gateway_external_ingress && !module.container_apps.contract.gateway_allow_insecure
    error_message = "Only LiteLLM may expose HTTPS ingress; the required Fusion app remains environment-only."
  }
  assert {
    condition     = module.container_apps.contract.fusion_runtime.target_port == 8080 && length(azurerm_role_assignment.fusion_secrets) == 3
    error_message = "Fusion must exist and receive only its independent bearer and two user secret grants."
  }
  assert {
    condition = (
      !jsondecode(output.gateway_config_json).general_settings.store_model_in_db &&
      !jsondecode(output.gateway_config_json).general_settings.forward_client_headers_to_llm_api &&
      jsondecode(output.gateway_config_json).general_settings.database_url == "os.environ/DATABASE_URL" &&
      length(azurerm_role_assignment.litellm_secrets) == 3
    )
    error_message = "Persist key/user data while disabling database models and client header forwarding."
  }
}

run "fixed_model_path_guard_and_no_alternate_routes" {
  command = plan
  assert {
    condition = (
      toset([for model in jsondecode(output.gateway_config_json).model_list : model.model_name]) == toset(["fusion-standard", "fusion-high", "fusion-max"]) &&
      alltrue([for model in jsondecode(output.gateway_config_json).model_list :
        model.litellm_params.model == "custom_openai/${model.model_name}" &&
        model.litellm_params.api_base == "os.environ/FUSION_API_BASE" &&
        model.litellm_params.api_key == "os.environ/FUSION_SERVICE_TOKEN" &&
        model.litellm_params.max_retries == 0
      ]) &&
      jsondecode(output.gateway_config_json).router_settings.disable_cooldowns &&
      jsondecode(output.gateway_config_json).router_settings.num_retries == 0 &&
      jsondecode(output.gateway_config_json).router_settings.max_fallbacks == 0 &&
      length(jsondecode(output.gateway_config_json).router_settings.fallbacks) == 0 &&
      length(jsondecode(output.gateway_config_json).router_settings.context_window_fallbacks) == 0 &&
      length(jsondecode(output.gateway_config_json).router_settings.content_policy_fallbacks) == 0 &&
      tolist(jsondecode(output.gateway_config_json).litellm_settings.callbacks) == tolist(["fusion_gateway_guard.guard"])
    )
    error_message = "Every public alias must use the private custom_openai adapter with the mandatory guard and no alternate paths."
  }
  assert {
    condition = (
      module.container_apps.contract.gateway.settings["PYTHONPATH"] == "/app" &&
      module.container_apps.contract.gateway.settings["FUSION_GUARD_SHA256"] == filesha256("../../services/litellm-fusion-gateway/fusion_gateway_guard.py") &&
      module.container_apps.contract.gateway.settings["STORE_MODEL_IN_DB"] == "False" &&
      length([for mount in module.container_apps.contract.gateway.volume_mounts : mount
        if mount.name == "fusion-guard" && mount.path == "/app/fusion_gateway_guard.py" && mount.sub_path == "fusion-guard"
      ]) == 1
    )
    error_message = "The fixed path guard must be importable and change the revision when its source changes."
  }
}

run "per_user_credentials_and_distinct_gateway_identity_secrets" {
  command = plan
  assert {
    condition = (
      length(module.container_apps.contract.fusion_runtime.credentials) == 2 &&
      module.container_apps.contract.fusion_runtime.credentials["user-a"].token_env != module.container_apps.contract.fusion_runtime.credentials["user-b"].token_env &&
      module.container_apps.contract.fusion_runtime.credentials["user-a"].expires_at == 4102444800 &&
      startswith(module.container_apps.contract.fusion_runtime.credentials["user-a"].token_env, "FUSION_GITHUB_USER_") &&
      length(module.container_apps.contract.fusion_runtime.secret_names) == 2
    )
    error_message = "Every user must receive a separate environment-secret binding and explicit expiry."
  }
  assert {
    condition = (
      toset(module.container_apps.contract.gateway.secret_names) == toset(["database-url", "master-key", "salt-key", "fusion-service-token", "fusion-identity-key"]) &&
      module.container_apps.contract.gateway.secret_settings["FUSION_SERVICE_TOKEN"] == "fusion-service-token" &&
      module.container_apps.contract.gateway.secret_settings["FUSION_IDENTITY_KEY"] == "fusion-identity-key" &&
      module.container_apps.contract.fusion_runtime.secret_settings["FUSION_SERVICE_TOKEN"] == "service-token" &&
      module.container_apps.contract.fusion_runtime.secret_settings["FUSION_IDENTITY_KEY"] == "identity-key" &&
      length(azurerm_role_assignment.fusion_identity_key) == 2 &&
      azurerm_role_assignment.litellm_fusion_bearer.scope == "/subscriptions/00000000-0000-0000-0000-000000000003/resourceGroups/secrets/providers/Microsoft.KeyVault/vaults/test-vault/secrets/service-token" &&
      alltrue([for role in azurerm_role_assignment.fusion_secrets : strcontains(role.scope, "/secrets/")])
    )
    error_message = "Gateway must receive bearer/signing secrets only, never user PATs; all user secret grants are secret-scoped."
  }
}

run "runtime_warm_capacity_probes_and_deadlines" {
  command = plan
  assert {
    condition = (
      module.container_apps.contract.fusion_runtime.min_replicas == 1 &&
      module.container_apps.contract.fusion_runtime.max_replicas == 1 &&
      module.container_apps.contract.fusion_runtime.settings["FUSION_TIMEOUT_SECONDS"] == "120" &&
      module.container_apps.contract.gateway.settings["FUSION_TIMEOUT_SECONDS"] == "140" &&
      module.container_apps.contract.fusion_runtime.settings["FUSION_MAX_CONCURRENCY"] == "2" &&
      module.container_apps.contract.fusion_runtime.http_concurrency == "2" &&
      module.container_apps.contract.fusion_runtime.termination_grace_seconds == 150
    )
    error_message = "Keep warm bounded capacity and one request deadline plus transport margin."
  }
  assert {
    condition = (
      module.container_apps.contract.fusion_runtime.startup_probe.path == "/health/live" &&
      module.container_apps.contract.fusion_runtime.startup_probe.interval_seconds * module.container_apps.contract.fusion_runtime.startup_probe.failure_count_threshold >= 300 &&
      module.container_apps.contract.fusion_runtime.liveness_probe.path == "/health/live" &&
      module.container_apps.contract.fusion_runtime.readiness_probe.path == "/health/ready" &&
      module.container_apps.contract.fusion_runtime.readiness_probe.timeout == 5
    )
    error_message = "Use non-inference liveness, SDK-aware readiness and sufficient startup grace."
  }
}

run "both_apps_share_one_environment" {
  command = plan
  override_resource {
    target          = module.container_apps.azurerm_container_app_environment.this
    override_during = plan
    values = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000003/resourceGroups/test-gateway-rg/providers/Microsoft.App/managedEnvironments/test-gateway-env"
    }
  }
  assert {
    condition     = module.container_apps.contract.same_environment
    error_message = "LiteLLM and Fusion must belong to the same ACA environment."
  }
}

run "duplicate_pat_scope_is_rejected_even_with_other_version_and_case" {
  command = plan
  variables {
    fusion_users = {
      user-a = { github_token_secret_id = "https://test-vault.vault.azure.net/secrets/user-token", expires_at = 4102444800 }
      user-b = { github_token_secret_id = "https://test-vault.vault.azure.net/secrets/USER-Token/00000000000000000000000000000001", expires_at = 4102444800 }
    }
  }
  expect_failures = [terraform_data.requirements]
}

run "service_token_cannot_be_user_pat" {
  command = plan
  variables {
    fusion_service_token_secret_id = "https://test-vault.vault.azure.net/secrets/USER-A-TOKEN/00000000000000000000000000000001"
  }
  expect_failures = [terraform_data.requirements]
}

run "expired_credentials_are_rejected" {
  command = plan
  variables {
    fusion_users = { user-a = { github_token_secret_id = "https://test-vault.vault.azure.net/secrets/user-a-token", expires_at = 946684800 } }
  }
  expect_failures = [terraform_data.requirements]
}

run "empty_user_mapping_is_rejected" {
  command = plan
  variables { fusion_users = {} }
  expect_failures = [var.fusion_users]
}

run "fractional_expiry_is_rejected" {
  command = plan
  variables {
    fusion_users = { user-a = { github_token_secret_id = "https://test-vault.vault.azure.net/secrets/user-a-token", expires_at = 4102444799.5 } }
  }
  expect_failures = [var.fusion_users]
}

run "secret_in_undeclared_vault_is_rejected" {
  command = plan
  variables {
    fusion_users = { user-a = { github_token_secret_id = "https://other-vault.vault.azure.net/secrets/user-a-token", expires_at = 4102444800 } }
  }
  expect_failures = [terraform_data.requirements]
}

run "mutable_fusion_image_is_rejected" {
  command = plan
  variables { fusion_image = "registry.example/fusion:latest" }
  expect_failures = [terraform_data.requirements]
}

run "fusion_can_scale_to_zero_and_use_full_single_path_deadline" {
  command = plan
  variables {
    fusion_min_replicas    = 0
    fusion_timeout_seconds = 210
    fusion_max_concurrency = 4
  }
  assert {
    condition = (
      module.container_apps.contract.fusion_runtime.min_replicas == 0 &&
      module.container_apps.contract.gateway.settings["FUSION_TIMEOUT_SECONDS"] == "230" &&
      module.container_apps.contract.fusion_runtime.settings["FUSION_TIMEOUT_SECONDS"] == "210" &&
      module.container_apps.contract.fusion_runtime.http_concurrency == "4" &&
      module.container_apps.contract.fusion_runtime.termination_grace_seconds == 240
    )
    error_message = "Cost-saving override may scale to zero while preserving the ACA deadline margin."
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

run "too_small_network_is_rejected" {
  command = plan
  variables { vnet_address_space = "10.40.0.0/24" }
  expect_failures = [var.vnet_address_space]
}

run "timeout_without_ingress_margin_is_rejected" {
  command = plan
  variables { fusion_timeout_seconds = 211 }
  expect_failures = [var.fusion_timeout_seconds]
}

run "unbounded_session_concurrency_is_rejected" {
  command = plan
  variables { fusion_max_concurrency = 0 }
  expect_failures = [var.fusion_max_concurrency]
}

run "invalid_user_subject_punctuation_is_rejected" {
  command = plan
  variables {
    fusion_users = { "user/a" = { github_token_secret_id = "https://test-vault.vault.azure.net/secrets/user-a-token", expires_at = 4102444800 } }
  }
  expect_failures = [var.fusion_users]
}

run "overlong_user_subject_is_rejected" {
  command = plan
  variables {
    fusion_users = { (join("", [for i in range(129) : "a"])) = { github_token_secret_id = "https://test-vault.vault.azure.net/secrets/user-a-token", expires_at = 4102444800 } }
  }
  expect_failures = [var.fusion_users]
}
