# Tools

Collection of automation scripts for ROSA cluster management and testing.

## [hcp-cluster-automation](hcp-cluster-automation/)

Create and tear down ROSA HCP (Hosted Control Plane) clusters on shared infrastructure. Supports multiple clusters per environment, standard public clusters, zero-egress (private/air-gapped) clusters, and AutoNode (Karpenter) autoscaling.

```bash
# Create shared infra (VPC, OIDC, account roles)
./infra-up.py -n my-env -r us-west-2

# Create clusters on shared infra
./up.py -c cluster-1 -f infra-my-env.json
./up.py -c cluster-2 -f infra-my-env.json -a    # With AutoNode

# Tear down
./down.py -c cluster-2 -f infra-my-env.json
./down.py -c cluster-1 -f infra-my-env.json
./infra-down.py -f infra-my-env.json

# Use a specific AWS profile
./infra-up.py -n my-env -r us-west-2 -p my-profile
./up.py -c cluster-1 -f infra-my-env.json -p my-profile
```

### State tracking

All state is tracked in JSON files so teardown works reliably even if a cluster was deleted externally.

- **`infra-<name>.json`** — Shared infrastructure state (VPC ID, subnet IDs, OIDC config ID, account role prefix, list of clusters). Created by `infra-up.py`, referenced by all other scripts.
- **`cluster-<name>.json`** — Per-cluster state (operator role prefix, autonode config, region). Created by `up.py`, used by `down.py` to clean up IAM resources.
- **`infra-<name>.tfstate`** — Terraform state for the VPC. Used by `infra-down.py` to destroy networking resources.

State files are gitignored and local to your working directory.

### Billing account

If your ROSA clusters bill to a different AWS account than where the infrastructure lives (e.g. a centralized billing org), pass `-b` during infra setup:

```bash
./infra-up.py -n my-env -r us-west-2 -b <billing-account-id>
```

The billing account ID is stored in the infra state file and automatically passed to `rosa create cluster` for all clusters created on that infrastructure. If omitted, the infrastructure account is used for billing.
