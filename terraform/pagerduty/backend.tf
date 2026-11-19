terraform {
  backend "s3" {
    bucket       = "integration-hub-production-pagerduty-tfstate"
    key          = "pagerduty/terraform.tfstate"
    region       = "eu-west-2"
    encrypt      = true
    kms_key_id   = "alias/s3/integration-hub-production-pagerduty-tfstate"
    use_lockfile = true
  }
}
