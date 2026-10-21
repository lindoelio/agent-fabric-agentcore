terraform {
  required_version = ">= 1.10, < 2.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.69.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = merge({ Project = var.project_name, ManagedBy = "Terraform" }, var.tags)
  }
}
