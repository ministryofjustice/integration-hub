variable "pagerduty_token" {
  description = "PagerDuty API token supplied at runtime from an approved secret store."
  type        = string
  sensitive   = true
  ephemeral   = true
}

variable "pagerduty_team_id" {
  description = "Optional override for the business services' owning team. Defaults to the Terraform-managed Integration Hub team."
  type        = string
  default     = null
}
