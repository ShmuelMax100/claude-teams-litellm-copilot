resource "azurerm_key_vault" "this" {
  name                       = var.name
  location                   = var.location
  resource_group_name        = var.resource_group_name
  tenant_id                  = var.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = true
  purge_protection_enabled   = true
  soft_delete_retention_days = 90
  tags                       = var.tags
}

# The deploying principal needs role-assignment permission. Runtime identities
# receive only secret-specific read roles in the root module.
resource "azurerm_role_assignment" "deployer" {
  scope                = azurerm_key_vault.this.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = var.administrator_object_id
}

resource "random_password" "master" {
  length  = 48
  special = false
}

resource "random_password" "salt" {
  length  = 64
  special = false
}

resource "azurerm_key_vault_secret" "master" {
  name         = "master-key"
  value        = "sk-${random_password.master.result}"
  key_vault_id = azurerm_key_vault.this.id
  content_type = "LiteLLM master key"
  tags         = var.tags
  depends_on   = [azurerm_role_assignment.deployer]
}

resource "azurerm_key_vault_secret" "salt" {
  name         = "salt-key"
  value        = random_password.salt.result
  key_vault_id = azurerm_key_vault.this.id
  content_type = "Stable LiteLLM encryption salt; preserve with database backups"
  tags         = var.tags
  depends_on   = [azurerm_role_assignment.deployer]
}

resource "azurerm_key_vault_secret" "database" {
  count        = var.create_database_secret ? 1 : 0
  name         = "database-url"
  value        = var.database_url
  key_vault_id = azurerm_key_vault.this.id
  content_type = "PostgreSQL connection URL requiring TLS"
  tags         = var.tags
  depends_on   = [azurerm_role_assignment.deployer]
}

output "id" { value = azurerm_key_vault.this.id }
output "name" { value = azurerm_key_vault.this.name }
output "master_key_secret_id" { value = azurerm_key_vault_secret.master.versionless_id }
output "salt_key_secret_id" { value = azurerm_key_vault_secret.salt.versionless_id }
output "database_secret_id" { value = var.create_database_secret ? azurerm_key_vault_secret.database[0].versionless_id : null }
