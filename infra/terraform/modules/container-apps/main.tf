resource "azurerm_container_app_environment" "this" {
  name                           = "${var.name_prefix}-env"
  location                       = var.location
  resource_group_name            = var.resource_group_name
  log_analytics_workspace_id     = var.log_analytics_workspace_id
  logs_destination               = "log-analytics"
  infrastructure_subnet_id       = var.subnet_id
  internal_load_balancer_enabled = var.private_environment
  public_network_access          = var.private_environment ? "Disabled" : "Enabled"
  tags                           = var.tags
  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }
}

locals {
  # JSON is YAML-compatible. All public models route to this private service.
  litellm_config = jsonencode({
    model_list = [for tier in ["standard", "high", "max"] : {
      model_name = "fusion-${tier}"
      litellm_params = {
        model       = "custom_openai/fusion-${tier}"
        api_base    = "os.environ/FUSION_API_BASE"
        api_key     = "os.environ/FUSION_SERVICE_TOKEN"
        max_retries = 0
        timeout     = var.fusion_timeout_seconds + 20
      }
    }]
    general_settings = {
      master_key                        = "os.environ/LITELLM_MASTER_KEY"
      database_url                      = "os.environ/DATABASE_URL"
      store_model_in_db                 = false
      trusted_proxy_ranges              = []
      disable_spend_logs                = false
      forward_client_headers_to_llm_api = false
    }
    litellm_settings = {
      json_logs                = true
      turn_off_message_logging = true
      callbacks                = ["fusion_gateway_guard.guard"]
    }
    router_settings = {
      # A user-specific expired PAT must not disable the shared deployment.
      disable_cooldowns        = true
      num_retries              = 0
      max_fallbacks            = 0
      fallbacks                = []
      context_window_fallbacks = []
      content_policy_fallbacks = []
    }
  })
  litellm_secrets = {
    master-key           = var.master_key_secret_id
    salt-key             = var.salt_key_secret_id
    database-url         = var.database_secret_id
    fusion-service-token = var.fusion_service_token_secret_id
    fusion-identity-key  = var.fusion_identity_key_secret_id
  }
  user_secrets = { for subject, user in var.fusion_users :
    "github-user-${substr(sha256(subject), 0, 16)}" => user.github_token_secret_id
  }
  user_credentials = { for subject, user in var.fusion_users : subject => {
    token_env  = "FUSION_GITHUB_USER_${upper(substr(sha256(subject), 0, 16))}"
    expires_at = user.expires_at
  } }
}

resource "azurerm_container_app" "litellm" {
  name                         = "${var.name_prefix}-litellm"
  container_app_environment_id = azurerm_container_app_environment.this.id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  tags                         = var.tags
  identity {
    type         = "UserAssigned"
    identity_ids = [var.litellm_identity_id]
  }
  dynamic "secret" {
    for_each = local.litellm_secrets
    content {
      name                = secret.key
      identity            = var.litellm_identity_id
      key_vault_secret_id = secret.value
    }
  }
  secret {
    name  = "litellm-config"
    value = local.litellm_config
  }
  # Nonsecret checked-in source is mounted into the digest-pinned gateway image.
  secret {
    name  = "fusion-guard"
    value = var.fusion_guard_source
  }
  dynamic "registry" {
    for_each = var.registry_server == null ? [] : [var.registry_server]
    content {
      server   = registry.value
      identity = var.litellm_identity_id
    }
  }
  ingress {
    external_enabled           = true
    allow_insecure_connections = false
    target_port                = 4000
    transport                  = "http"
    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
    dynamic "ip_security_restriction" {
      for_each = { for i, cidr in var.gateway_allowed_cidrs : tostring(i) => cidr }
      content {
        name             = "allowed-${ip_security_restriction.key}"
        action           = "Allow"
        ip_address_range = ip_security_restriction.value
      }
    }
  }
  template {
    min_replicas = var.litellm_min_replicas
    max_replicas = var.litellm_max_replicas
    http_scale_rule {
      name                = "http"
      concurrent_requests = tostring(var.litellm_http_concurrency)
    }
    volume {
      name         = "configuration"
      storage_type = "Secret"
    }
    volume {
      name         = "fusion-guard"
      storage_type = "Secret"
    }
    container {
      name   = "litellm"
      image  = var.litellm_image
      cpu    = var.litellm_cpu
      memory = var.litellm_memory
      args   = ["--config", "/etc/litellm/litellm-config", "--port", "4000"]
      volume_mounts {
        name = "configuration"
        path = "/etc/litellm"
      }
      volume_mounts {
        name     = "fusion-guard"
        path     = "/app/fusion_gateway_guard.py"
        sub_path = "fusion-guard"
      }
      dynamic "env" {
        for_each = {
          LITELLM_MASTER_KEY   = "master-key"
          LITELLM_SALT_KEY     = "salt-key"
          DATABASE_URL         = "database-url"
          FUSION_SERVICE_TOKEN = "fusion-service-token"
          FUSION_IDENTITY_KEY  = "fusion-identity-key"
        }
        content {
          name        = env.key
          secret_name = env.value
        }
      }
      dynamic "env" {
        for_each = {
          PYTHONPATH             = "/app"
          FUSION_API_BASE        = "https://${azurerm_container_app.fusion.ingress[0].fqdn}/v1"
          FUSION_TIMEOUT_SECONDS = tostring(var.fusion_timeout_seconds + 20)
          FUSION_GUARD_SHA256    = sha256(var.fusion_guard_source)
        }
        content {
          name  = env.key
          value = env.value
        }
      }
      # Secret updates alone do not create revisions; config changes must.
      env {
        name  = "LITELLM_CONFIG_SHA256"
        value = sha256(local.litellm_config)
      }
      env {
        name  = "STORE_MODEL_IN_DB"
        value = "False"
      }
      env {
        name  = "LITELLM_LOG"
        value = var.litellm_log_level
      }
      startup_probe {
        transport               = "HTTP"
        path                    = "/health/liveliness"
        port                    = 4000
        interval_seconds        = 10
        timeout                 = 5
        failure_count_threshold = 30
      }
      liveness_probe {
        transport        = "HTTP"
        path             = "/health/liveliness"
        port             = 4000
        interval_seconds = 30
        timeout          = 5
      }
      readiness_probe {
        transport               = "HTTP"
        path                    = "/health/readiness"
        port                    = 4000
        interval_seconds        = 10
        timeout                 = 5
        failure_count_threshold = 6
      }
    }
  }
}

resource "azurerm_container_app" "fusion" {
  name                         = "${var.name_prefix}-fusion"
  container_app_environment_id = azurerm_container_app_environment.this.id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  tags                         = var.tags
  identity {
    type         = "UserAssigned"
    identity_ids = [var.fusion_identity_id]
  }
  dynamic "secret" {
    for_each = merge(local.user_secrets, { service-token = var.fusion_service_token_secret_id, identity-key = var.fusion_identity_key_secret_id })
    content {
      name                = secret.key
      identity            = var.fusion_identity_id
      key_vault_secret_id = secret.value
    }
  }
  dynamic "registry" {
    for_each = var.registry_server == null ? [] : [var.registry_server]
    content {
      server   = registry.value
      identity = var.fusion_identity_id
    }
  }
  ingress {
    external_enabled           = false
    allow_insecure_connections = false
    target_port                = 8080
    transport                  = "http"
    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }
  template {
    min_replicas                     = var.fusion_min_replicas
    max_replicas                     = var.fusion_max_replicas
    termination_grace_period_seconds = var.fusion_timeout_seconds + 30
    http_scale_rule {
      name                = "http"
      concurrent_requests = tostring(var.fusion_max_concurrency)
    }
    container {
      name   = "fusion"
      image  = var.fusion_image
      cpu    = var.fusion_cpu
      memory = var.fusion_memory
      env {
        name  = "FUSION_USER_CREDENTIALS_JSON"
        value = jsonencode(local.user_credentials)
      }
      dynamic "env" {
        for_each = merge({
          FUSION_SERVICE_TOKEN = "service-token"
          FUSION_IDENTITY_KEY  = "identity-key"
          }, { for subject, credential in local.user_credentials :
          credential.token_env => "github-user-${substr(sha256(subject), 0, 16)}"
        })
        content {
          name        = env.key
          secret_name = env.value
        }
      }
      env {
        name  = "FUSION_TIMEOUT_SECONDS"
        value = tostring(var.fusion_timeout_seconds)
      }
      env {
        name  = "FUSION_MAX_CONCURRENCY"
        value = tostring(var.fusion_max_concurrency)
      }
      # The image bundles the pinned SDK/runtime. Its stdio connection remains
      # inside this container: no additional ingress port or sidecar is needed.
      # Allow local runtime initialization without making liveness an inference
      # check. Readiness below requires the service's SDK protocol ping.
      startup_probe {
        transport               = "HTTP"
        path                    = "/health/live"
        port                    = 8080
        interval_seconds        = 10
        timeout                 = 5
        failure_count_threshold = 30
      }
      liveness_probe {
        transport        = "HTTP"
        path             = "/health/live"
        port             = 8080
        interval_seconds = 30
      }
      readiness_probe {
        transport               = "HTTP"
        path                    = "/health/ready"
        port                    = 8080
        initial_delay           = 5
        interval_seconds        = 15
        timeout                 = 5
        failure_count_threshold = 6
        success_count_threshold = 1
      }
    }
  }
}

moved {
  from = azurerm_container_app.fusion[0]
  to   = azurerm_container_app.fusion
}

output "gateway_url" { value = "https://${azurerm_container_app.litellm.ingress[0].fqdn}" }
output "fusion_mcp_url" { value = "https://${azurerm_container_app.fusion.ingress[0].fqdn}/mcp" }
output "default_domain" { value = azurerm_container_app_environment.this.default_domain }
output "static_ip_address" { value = azurerm_container_app_environment.this.static_ip_address }
# Nonsecret planned settings allow explicit mocked root tests.
output "contract" {
  value = {
    same_environment         = azurerm_container_app.litellm.container_app_environment_id == azurerm_container_app.fusion.container_app_environment_id
    fusion_external_ingress  = azurerm_container_app.fusion.ingress[0].external_enabled
    gateway_external_ingress = azurerm_container_app.litellm.ingress[0].external_enabled
    gateway_allow_insecure   = azurerm_container_app.litellm.ingress[0].allow_insecure_connections
    private_environment      = var.private_environment
    config                   = local.litellm_config
    gateway = {
      secret_names  = keys(local.litellm_secrets)
      volume_mounts = azurerm_container_app.litellm.template[0].container[0].volume_mounts
      settings = { for setting in azurerm_container_app.litellm.template[0].container[0].env : setting.name => setting.value
        if contains(["PYTHONPATH", "FUSION_GUARD_SHA256", "FUSION_API_BASE", "FUSION_TIMEOUT_SECONDS", "STORE_MODEL_IN_DB"], setting.name)
      }
      secret_settings = { for setting in azurerm_container_app.litellm.template[0].container[0].env : setting.name => setting.secret_name
        if contains(["FUSION_SERVICE_TOKEN", "FUSION_IDENTITY_KEY"], setting.name)
      }
    }
    fusion_runtime = {
      min_replicas              = azurerm_container_app.fusion.template[0].min_replicas
      max_replicas              = azurerm_container_app.fusion.template[0].max_replicas
      termination_grace_seconds = azurerm_container_app.fusion.template[0].termination_grace_period_seconds
      target_port               = azurerm_container_app.fusion.ingress[0].target_port
      http_concurrency          = azurerm_container_app.fusion.template[0].http_scale_rule[0].concurrent_requests
      startup_probe             = azurerm_container_app.fusion.template[0].container[0].startup_probe[0]
      liveness_probe            = azurerm_container_app.fusion.template[0].container[0].liveness_probe[0]
      readiness_probe           = azurerm_container_app.fusion.template[0].container[0].readiness_probe[0]
      credentials               = local.user_credentials
      secret_names              = keys(local.user_secrets)
      settings = { for setting in azurerm_container_app.fusion.template[0].container[0].env : setting.name => setting.value
        if contains(["FUSION_TIMEOUT_SECONDS", "FUSION_MAX_CONCURRENCY"], setting.name)
      }
      secret_settings = { for setting in azurerm_container_app.fusion.template[0].container[0].env : setting.name => setting.secret_name
        if setting.secret_name != "" && setting.secret_name != null
      }
    }
  }
}
