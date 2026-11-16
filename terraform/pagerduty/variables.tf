variable "pagerduty_token" {
  description = "PagerDuty API token supplied at runtime from an approved secret store."
  type        = string
  sensitive   = true
  ephemeral   = true
}

variable "pagerduty_team_id" {
  description = "Optional PagerDuty team ID to assign to the Integration Hub business services."
  type        = string
  default     = null
}
