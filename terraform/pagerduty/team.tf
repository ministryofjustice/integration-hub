resource "pagerduty_team" "integration_hub" {
  name        = "Integration Hub"
  description = "Owns and manages Integration Hub PagerDuty services, incidents, and status page communications."
}

locals {
  pagerduty_users = {
    "david.sibley" : {
      email     = "david.sibley@digital.justice.gov.uk"
      role      = "manager"
      user_name = "david.sibley"
    }
    "sreelakshmi.ramesh" : {
      email     = "sreelakshmi.ramesh@justice.gov.uk"
      role      = "manager"
      user_name = "sreelakshmi.ramesh"
    }
  }
}

data "pagerduty_user" "users" {
  for_each = local.pagerduty_users
  email    = each.value.email
}

resource "pagerduty_team_membership" "integration_hub" {
  for_each = data.pagerduty_user.users
  team_id  = pagerduty_team.integration_hub.id
  user_id  = each.value.id
  role     = local.pagerduty_users[each.key].role
}
