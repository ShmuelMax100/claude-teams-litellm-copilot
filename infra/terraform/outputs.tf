output "gateway_url" {
  description = "HTTPS origin to enter in ./setup admin; private environments require customer network connectivity."
  value       = module.container_apps.gateway_url
}

output "fusion_mcp_url" {
  description = "Environment-only HTTPS MCP endpoint. Register explicitly; no developer keys are changed by Terraform."
  value       = module.container_apps.fusion_mcp_url
}

output "resource_group_name" { value = azurerm_resource_group.this.name }
output "key_vault_name" { value = module.key_vault.name }
output "key_vault_id" { value = module.key_vault.id }
output "litellm_identity_id" { value = azurerm_user_assigned_identity.litellm.id }
output "virtual_network_id" { value = azurerm_virtual_network.this.id }
output "postgresql_server_id" { value = var.enable_managed_postgresql ? module.postgresql[0].id : null }
output "master_key_secret_name" {
  description = "Retrieve this secret privately through an authorized operator workflow; never paste it in chat or logs."
  value       = "master-key"
}

output "gateway_config_json" {
  description = "Nonsecret active config for audited copilot-only --server-config validation; values are environment references, not credentials."
  value       = module.container_apps.contract.config
}
