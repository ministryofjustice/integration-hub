terraform {
  backend "s3" {
    bucket       = "integration-hub-production-pagerduty-tfstate"
    key          = "pagerduty/terraform.tfstate"
    region       = "eu-west-2"
    encrypt      = true
    use_lockfile = true
  }
}
