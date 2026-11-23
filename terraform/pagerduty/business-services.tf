locals {
  business_services = {
    mft = {
      name        = "Integration Hub Managed File Transfer"
      description = "Integration Hub managed file transfer"
    }
    api_platform = {
      name        = "Integration Hub API Platform"
      description = "Integration Hub API platform"
    }
    events_platform = {
      name        = "Integration Hub Events platform"
      description = "Integration Hub events platform"
    }
  }
}

resource "pagerduty_business_service" "integration_hub" {
  for_each = local.business_services

  name        = each.value.name
  description = each.value.description
  team        = coalesce(var.pagerduty_team_id, pagerduty_team.integration_hub.id)
}
