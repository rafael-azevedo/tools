terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.53.0"
    }
  }

  required_version = ">= 1.8.5"
}

provider "aws" {
  region     = var.region
  sts_region = "us-east-1"
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = ">= 5.8.1"

  name = "${var.env_name}-vpc"
  cidr = var.vpc_cidr_block

  azs             = var.availability_zones
  private_subnets = var.private_subnets
  public_subnets  = var.zero_egress ? [] : var.public_subnets

  private_subnet_tags = {
    "kubernetes.io/role/internal-elb" = "1"
  }

  public_subnet_tags = {
    "kubernetes.io/role/elb" = "1"
  }

  enable_nat_gateway   = var.zero_egress ? false : true
  single_nat_gateway   = true
  enable_dns_hostnames = true
  enable_dns_support   = true

  manage_default_security_group = false

  tags = {
    Terraform    = "true"
    service      = "ROSA"
    env_name = var.env_name
  }
}

# Zero-egress: security group for VPC endpoint inbound traffic
resource "aws_security_group" "authorize_inbound_vpc_traffic" {
  count  = var.zero_egress ? 1 : 0
  vpc_id = module.vpc.vpc_id

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = var.private_subnets
  }
}

# Zero-egress: VPC endpoints
resource "aws_vpc_endpoint" "sts" {
  count             = var.zero_egress ? 1 : 0
  service_name      = "com.amazonaws.${var.region}.sts"
  vpc_id            = module.vpc.vpc_id
  vpc_endpoint_type = "Interface"

  private_dns_enabled = true
  subnet_ids          = module.vpc.private_subnets
  security_group_ids  = [aws_security_group.authorize_inbound_vpc_traffic[0].id]

  tags = {
    Terraform    = "true"
    service      = "ROSA"
    env_name = var.env_name
  }
}

resource "aws_vpc_endpoint" "ecr_api" {
  count             = var.zero_egress ? 1 : 0
  service_name      = "com.amazonaws.${var.region}.ecr.api"
  vpc_id            = module.vpc.vpc_id
  vpc_endpoint_type = "Interface"

  private_dns_enabled = true
  subnet_ids          = module.vpc.private_subnets
  security_group_ids  = [aws_security_group.authorize_inbound_vpc_traffic[0].id]

  tags = {
    Terraform    = "true"
    service      = "ROSA"
    env_name = var.env_name
  }
}

resource "aws_vpc_endpoint" "ecr_dkr" {
  count             = var.zero_egress ? 1 : 0
  service_name      = "com.amazonaws.${var.region}.ecr.dkr"
  vpc_id            = module.vpc.vpc_id
  vpc_endpoint_type = "Interface"

  private_dns_enabled = true
  subnet_ids          = module.vpc.private_subnets
  security_group_ids  = [aws_security_group.authorize_inbound_vpc_traffic[0].id]

  tags = {
    Terraform    = "true"
    service      = "ROSA"
    env_name = var.env_name
  }
}

resource "aws_vpc_endpoint" "s3" {
  count             = var.zero_egress ? 1 : 0
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_id            = module.vpc.vpc_id
  vpc_endpoint_type = "Gateway"

  route_table_ids = module.vpc.private_route_table_ids

  tags = {
    Terraform    = "true"
    service      = "ROSA"
    env_name = var.env_name
  }
}

# Variables

variable "region" {
  default = "us-east-1"
}

variable "availability_zones" {
  type    = list(any)
  default = ["us-east-1a"]
}

variable "private_subnets" {
  type    = list(any)
  default = ["10.0.0.0/24"]
}

variable "public_subnets" {
  type    = list(any)
  default = ["10.0.128.0/24"]
}

variable "vpc_cidr_block" {
  type    = string
  default = "10.0.0.0/16"
}

variable "zero_egress" {
  type    = bool
  default = false
}

variable "env_name" {
  description = "Name of the shared environment (used for VPC and resource naming)."
  type        = string
  default     = "my-hcp-env"

  validation {
    condition     = can(regex("^[a-z][-a-z0-9]{0,13}[a-z0-9]$", var.env_name))
    error_message = <<-EOT
      Environment names must be less than 16 characters.
        May only contain lower case, alphanumeric, or hyphens characters.
    EOT
  }
}

# Outputs

output "vpc_id" {
  value       = module.vpc.vpc_id
  description = "VPC ID"
}

output "private_subnet_ids" {
  value       = jsonencode(module.vpc.private_subnets)
  description = "Private subnet IDs"
}

output "public_subnet_ids" {
  value       = jsonencode(module.vpc.public_subnets)
  description = "Public subnet IDs"
}
