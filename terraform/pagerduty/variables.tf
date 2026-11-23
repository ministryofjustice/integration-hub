variable "pagerduty_token" {
  description = "PagerDuty API token supplied at runtime from an approved secret store."
  type        = string
  sensitive   = true
  ephemeral   = true
}
