# HCP Cluster Automation

Scripts to create and tear down ROSA HCP (Hosted Control Plane) clusters on AWS.

Supports multiple clusters on shared infrastructure (VPC, OIDC config, account roles).

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

### 1. Create shared infrastructure

```bash
# Standard environment
./infra-up.py -n my-env -r us-west-2

# Zero-egress environment
./infra-up.py -n my-env -r us-west-2 -z

# With specific AWS profile and billing account
./infra-up.py -n my-env -r us-east-1 -p my-profile -b 123456789012
```

This creates a VPC, OIDC config, and account roles. State is saved to `infra-<name>.json`.

### 2. Create clusters on shared infrastructure

```bash
# Create a cluster
./up.py -c cluster-1 -f infra-my-env.json

# Create another cluster on the same infrastructure
./up.py -c cluster-2 -f infra-my-env.json -v 4.18.10

# Create a cluster with AutoNode (Karpenter)
./up.py -c cluster-3 -f infra-my-env.json -a
```

Each cluster gets its own operator roles and state file (`cluster-<name>.json`).

### 3. Tear down individual clusters

```bash
./down.py -c cluster-2 -f infra-my-env.json
./down.py -c cluster-1 -f infra-my-env.json
./down.py -c cluster-3 -f infra-my-env.json
```

Cleans up operator roles, AutoNode IAM resources, and the ROSA cluster. Works even if the cluster was already deleted externally.

### 4. Tear down shared infrastructure

```bash
./infra-down.py -f infra-my-env.json
```

Destroys VPC, OIDC config, and account roles. Refuses if clusters still exist (use `--force` to override).

## Options

### infra-up.py

| Flag | Description |
|------|-------------|
| `-n, --name` | Environment name (required, max 15 chars, lowercase alphanumeric + hyphens) |
| `-r, --region` | AWS region (default: us-east-1) |
| `-p, --profile` | AWS CLI profile to use |
| `-z, --zero-egress` | Create zero-egress VPC (private, no internet) |
| `-b, --billing-account` | AWS billing account ID (default: infrastructure account) |

### up.py

| Flag | Description |
|------|-------------|
| `-c, --cluster-name` | Cluster name (required, max 15 chars, lowercase alphanumeric + hyphens) |
| `-f, --infra-file` | Path to infra state file from infra-up.py (required) |
| `-v, --version` | OpenShift version (default: latest available) |
| `-p, --profile` | AWS CLI profile to use |
| `-a, --autonode` | Enable AutoNode (Karpenter) |

### down.py

| Flag | Description |
|------|-------------|
| `-c, --cluster-name` | Cluster name to tear down (required) |
| `-f, --infra-file` | Path to infra state file (required) |
| `-p, --profile` | AWS CLI profile to use |

### infra-down.py

| Flag | Description |
|------|-------------|
| `-f, --infra-file` | Path to infra state file (required) |
| `-p, --profile` | AWS CLI profile to use |
| `--force` | Skip cluster check and force teardown |

## What each mode creates

### Shared infrastructure (infra-up.py)

- VPC with public and private subnets (or private-only for zero-egress)
- Internet gateway + NAT gateway (standard) or VPC endpoints (zero-egress)
- Shared OIDC config (reusable across clusters)
- Shared account roles (prefixed with environment name)

### Per-cluster (up.py)

- Operator roles (prefixed with cluster name, linked to shared OIDC config)
- ROSA HCP cluster on the shared VPC
- ECR ReadOnly policy attachment (zero-egress environments)

### AutoNode (`-a`)

All of the above plus:
- Pins cluster to tech-preview shard (region auto-detected)
- Creates Karpenter IAM policy and role with OIDC trust
- Enables AutoNode via OCM API patch
- Tags subnets and security groups for Karpenter discovery

## State files

| File | Purpose |
|------|---------|
| `infra-<name>.json` | Shared infrastructure state (VPC, OIDC, account roles, cluster list) |
| `infra-<name>.tfstate` | Terraform state for the VPC |
| `cluster-<name>.json` | Per-cluster state (operator roles, autonode config) |

## Script files

| File | Purpose |
|------|---------|
| `infra-up.py` | Create shared infrastructure |
| `up.py` | Create a cluster on existing infrastructure |
| `down.py` | Tear down a single cluster |
| `infra-down.py` | Tear down shared infrastructure |
| `main.tf` | Terraform config for VPC (standard and zero-egress) |
| `autonode-policy.json` | IAM policy for Karpenter controller |
