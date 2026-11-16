terraform {
  required_version = ">= 1.10.0, < 2.0.0"

  required_providers {
    pagerduty = {
      source  = "pagerduty/pagerduty"
      version = "~> 3.35"
    }
  }
}
