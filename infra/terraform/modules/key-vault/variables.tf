variable "name" {
  type = string
}

variable "location" {
  type = string
}

variable "resource_group_name" {
  type = string
}

variable "tenant_id" {
  type = string
}

variable "administrator_object_id" {
  type = string
}

variable "create_database_secret" {
  type = bool
}

variable "database_url" {
  type      = string
  sensitive = true
}

variable "tags" {
  type = map(string)
}
