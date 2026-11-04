locals {
  business_services = {
    mft = {
      name        = "Managed File Transfer"
      description = "Integration Hub managed file transfer"
    }
    api_platform = {
      name        = "API Platform"
      description = "Integration Hub API platform"
    }
    events_platform = {
      name        = "Events platform"
      description = "Integration Hub events platform"
    }
  }
}

resource "pagerduty_business_service" "integration_hub" {
  for_each = local.business_services

  name        = each.value.name
  description = each.value.description
  team        = var.pagerduty_team_id
}
