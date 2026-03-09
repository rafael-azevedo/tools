#!/usr/bin/env python3

import json
import os
import glob

from optparse import OptionParser


def shell(command):
    print(command)
    f = os.popen(command)
    stdout = f.read()
    error = f.close()
    print(stdout)
    if error:
        raise RuntimeError(stdout)
    return stdout


usage = """
usage: %prog [options]
Tears down a single ROSA HCP cluster.

Cleans up IAM resources even if the cluster was already deleted externally.
Does NOT remove shared infrastructure (OIDC config, account roles, VPC).

Use infra-down.py to remove shared infrastructure after all clusters are gone.
"""

parser = OptionParser(usage=usage)
parser.add_option("-c", "--cluster-name", dest="name", help="Cluster name to tear down")
parser.add_option("-f", "--infra-file", dest="infra_file", help="Path to infra state file (from infra-up.py)")
parser.add_option("-p", "--profile", dest="profile", default=None, help="AWS profile to use")

(options, args) = parser.parse_args()

cluster_name = options.name
infra_file = options.infra_file

if not cluster_name or not infra_file:
    parser.print_help()
    exit(0)

if options.profile:
    os.environ["AWS_PROFILE"] = options.profile

script_dir = os.path.dirname(os.path.abspath(__file__))

# Load infra state
with open(infra_file) as f:
    infra = json.load(f)

account_id = infra["account_id"]
account_role_prefix = infra["account_role_prefix"]
zero_egress = infra.get("zero_egress", False)

# Load per-cluster state if it exists
cluster_state_file = os.path.join(script_dir, f"cluster-{cluster_name}.json")
autonode = False
if os.path.exists(cluster_state_file):
    with open(cluster_state_file) as f:
        cluster_state = json.load(f)
    autonode = cluster_state.get("autonode", False)

# Delete the cluster (continue if already gone)
print(f"Deleting cluster: {cluster_name}")
try:
    shell(f"rosa delete cluster --cluster={cluster_name} --yes --watch")
except RuntimeError:
    print(f"Cluster '{cluster_name}' not found or already deleted, continuing cleanup...")

# Delete operator roles (uses cluster name prefix - doesn't need the cluster to exist)
print("Deleting operator roles...")
try:
    shell(f"rosa delete operator-roles --prefix={cluster_name} --mode=auto --yes")
except RuntimeError:
    print("Operator roles already deleted or not found.")

# Clean up zero-egress specific resources
if zero_egress:
    print("Cleaning up zero-egress ECR policy...")
    try:
        shell(
            f"aws iam detach-role-policy --role-name {account_role_prefix}-HCP-ROSA-Worker-Role "
            f"--policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
        )
    except RuntimeError:
        print("ECR policy already detached or worker role already deleted.")

# Clean up AutoNode resources
if autonode:
    role_name = f"{cluster_name}-autonode-operator-role"
    policy_arn = f"arn:aws:iam::{account_id}:policy/{cluster_name}-autonode"

    print("Cleaning up AutoNode IAM resources...")
    try:
        shell(f"aws iam detach-role-policy --role-name {role_name} --policy-arn {policy_arn}")
    except RuntimeError:
        print("AutoNode policy already detached.")
    try:
        shell(f"aws iam delete-role --role-name {role_name}")
    except RuntimeError:
        print("AutoNode role already deleted.")
    try:
        shell(f"aws iam delete-policy --policy-arn {policy_arn}")
    except RuntimeError:
        print("AutoNode policy already deleted.")

# Clean up generated temp files (trust policy, patch files)
for f in glob.glob(os.path.join(script_dir, f"{cluster_name}-*.json")):
    os.remove(f)
    print(f"Removed {f}")

# Remove cluster from infra state
if cluster_name in infra.get("clusters", []):
    infra["clusters"].remove(cluster_name)
    with open(infra_file, "w") as f:
        json.dump(infra, f, indent=2)
    print(f"Removed '{cluster_name}' from {infra_file}")

# Delete per-cluster state file
if os.path.exists(cluster_state_file):
    os.remove(cluster_state_file)
    print(f"Removed {cluster_state_file}")

print(f"\nCluster '{cluster_name}' teardown complete.")
remaining = infra.get("clusters", [])
if remaining:
    print(f"Remaining clusters in environment: {', '.join(remaining)}")
else:
    print(f"No clusters remaining. Run infra-down.py to remove shared infrastructure.")
