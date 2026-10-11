terraform {
  required_version = ">= 1.9"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.66.0" }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project   = "yatra-pulse"
      ManagedBy = "terraform"
      Teardown  = "deploy/aws/teardown.sh"
    }
  }
}
