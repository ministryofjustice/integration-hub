data "pagerduty_service" "file_transfer_high_priority" {
  name = "Integration Hub File Transfer High Priority Alerts"
}

resource "pagerduty_service_dependency" "managed_file_transfer" {
  dependency {
    dependent_service {
      id   = pagerduty_business_service.integration_hub["mft"].id
      type = "business_service"
    }

    supporting_service {
      id   = data.pagerduty_service.file_transfer_high_priority.id
      type = data.pagerduty_service.file_transfer_high_priority.type
    }
  }
}
