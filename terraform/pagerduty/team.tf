resource "pagerduty_team" "integration_hub" {
  name        = "Integration Hub"
  description = "Owns and manages Integration Hub PagerDuty services, incidents, and status page communications."
}
