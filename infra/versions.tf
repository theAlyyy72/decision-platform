terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.0"
    }
  }
}

variable "region" {
  description = "The Region your project was provisioned in (AWS Settings > Project > Additional info)."
  type        = string
  default     = "ap-southeast-2"
}

variable "project" {
  description = "Prefix for resource names."
  type        = string
  default     = "decision-platform"
}

# Credentials come from the environment (aws configure export-credentials), never from this code.
provider "aws" {
  region = var.region

  default_tags {
    tags = {
      project    = var.project
      managed_by = "terraform"
    }
  }
}
