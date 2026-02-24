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
Inputs:
- Cluster name
- Flags matching how the cluster was created (-a for autonode, -z for zero-egress)
Outputs:
- Deletes the ROSA HCP cluster and associated IAM resources
- Optionally destroys the VPC infrastructure
"""

parser = OptionParser(usage=usage)
parser.add_option("-c", "--cluster-name", dest="name", help="Hosted cluster name")
parser.add_option("-p", "--profile", dest="profile", default=None, help="AWS profile to use")
parser.add_option("-a", "--autonode", dest="autonode", action="store_true", default=False, help="Clean up AutoNode IAM resources")
parser.add_option("-z", "--zero-egress", dest="zero_egress", action="store_true", default=False, help="Clean up zero-egress specific resources")
parser.add_option("-x", "--destroy-infra", dest="destroy_infra", action="store_true", default=False,
                  help="Also destroy terraform infrastructure (VPC, subnets, etc.)")

(options, args) = parser.parse_args()

cluster_name = options.name

if not cluster_name:
    parser.print_help()
    exit(0)

if options.profile:
    os.environ["AWS_PROFILE"] = options.profile

script_dir = os.path.dirname(os.path.abspath(__file__))

whoami = json.loads(shell("rosa whoami -ojson"))
account_id = whoami["AWS Account ID"]

# Delete the cluster
print(f"Deleting cluster: {cluster_name}")
try:
    shell(f"rosa delete cluster --cluster={cluster_name} --yes --watch")
except RuntimeError:
    print(f"Cluster '{cluster_name}' not found or already deleted, continuing cleanup...")

# Delete ROSA IAM resources
print("Deleting account roles...")
shell(f"rosa delete account-roles --prefix={cluster_name} --mode=auto --yes")

print("Deleting operator roles...")
shell(f"rosa delete operator-roles --prefix={cluster_name} --mode=auto --yes")

print("Listing OIDC configs (manual cleanup may be needed)...")
shell("rosa list oidc-config")

# Clean up zero-egress specific resources
if options.zero_egress:
    print("Cleaning up zero-egress resources...")
    try:
        shell(
            f"aws iam detach-role-policy --role-name {cluster_name}-HCP-ROSA-Worker-Role "
            f"--policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
        )
    except RuntimeError:
        print("ECR policy already detached or worker role already deleted.")

# Clean up AutoNode resources
if options.autonode:
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

# Clean up generated temp files
for f in glob.glob(os.path.join(script_dir, f"{cluster_name}-*.json")):
    os.remove(f)
    print(f"Removed {f}")

# Destroy infrastructure
if options.destroy_infra:
    print("Destroying terraform infrastructure...")
    shell(f"terraform -chdir={script_dir} destroy -auto-approve")
else:
    print("Skipping infrastructure destruction. Use -x flag to also destroy VPC.")
