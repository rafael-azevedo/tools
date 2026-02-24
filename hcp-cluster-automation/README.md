# HCP Cluster Automation

Scripts to create and tear down ROSA HCP (Hosted Control Plane) clusters on AWS.

Supports three modes:
- **Standard HCP** - Public cluster with NAT gateway
- **Zero-Egress** - Private cluster with no internet access, uses VPC endpoints
- **AutoNode** - Enables Karpenter autoscaling (can be combined with either mode)

## Prerequisites

- `rosa` CLI logged in (`rosa login`)
- `aws` CLI configured with valid credentials
- `terraform` >= 1.8.5
- `ocm` CLI logged in (required for AutoNode only)

## Usage

### Create a cluster

```bash
# Standard HCP cluster
./up.py -c my-cluster -r us-east-1

# Standard HCP with AutoNode (Karpenter)
./up.py -c my-cluster -a

# Zero-egress cluster (private, no internet)
./up.py -c my-cluster -r us-west-2 -z

# Zero-egress with AutoNode
./up.py -c my-cluster -z -a

# Specify OCP version and billing account
./up.py -c my-cluster -r us-east-1 -v 4.17.15 -b 123456789012

# Use a specific AWS profile
./up.py -c my-cluster -r us-east-1 -p my-aws-profile
```

### Tear down a cluster

Use the same flags you used to create:

```bash
# Standard HCP
./down.py -c my-cluster -x

# AutoNode
./down.py -c my-cluster -a -x

# Zero-egress
./down.py -c my-cluster -z -x

# Zero-egress + AutoNode
./down.py -c my-cluster -z -a -x
```

Omit `-x` to keep the VPC infrastructure and only delete the cluster and IAM resources.

### Options

#### up.py

| Flag | Description |
|------|-------------|
| `-c, --cluster-name` | Cluster name (required, max 15 chars, lowercase alphanumeric + hyphens) |
| `-r, --region` | AWS region (default: us-east-1) |
| `-v, --version` | OpenShift version (default: latest available) |
| `-p, --profile` | AWS CLI profile to use |
| `-b, --billing-account` | AWS billing account ID (default: infrastructure account) |
| `-a, --autonode` | Enable AutoNode (Karpenter) |
| `-z, --zero-egress` | Create a zero-egress cluster |

#### down.py

| Flag | Description |
|------|-------------|
| `-c, --cluster-name` | Cluster name (required) |
| `-p, --profile` | AWS CLI profile to use |
| `-a, --autonode` | Clean up AutoNode IAM resources |
| `-z, --zero-egress` | Clean up zero-egress resources |
| `-x, --destroy-infra` | Also destroy VPC infrastructure via Terraform |

## What each mode creates

### Standard HCP

- VPC with public and private subnets
- Internet gateway + NAT gateway
- Public ROSA HCP cluster

### Zero-Egress (`-z`)

- VPC with private subnets only (no public subnets)
- No NAT gateway, no internet gateway
- VPC endpoints for STS, ECR (API + DKR), and S3
- Security group for VPC endpoint traffic
- Private ROSA HCP cluster with `zero_egress:true` property
- ECR ReadOnly policy attached to worker role

### AutoNode (`-a`)

All of the above plus:
- Pins cluster to tech-preview shard (region auto-detected)
- Creates Karpenter IAM policy and role with OIDC trust
- Enables AutoNode via OCM API patch
- Tags subnets and security groups for Karpenter discovery

After AutoNode is enabled, create workloads via the Kubernetes API:
1. Inspect the default `OpenshiftEC2NodeClass`
2. Create a `NodePool` to define instance types and capacity
3. Deploy workloads with `nodeSelector` to trigger autoscaling

## Files

| File | Purpose |
|------|---------|
| `up.py` | Create cluster and infrastructure |
| `down.py` | Tear down cluster and infrastructure |
| `main.tf` | Terraform config (handles both standard and zero-egress) |
| `autonode-policy.json` | IAM policy for Karpenter controller |
