#!/usr/bin/env python3

import json
import os
import subprocess
import sys

from optparse import OptionParser


def shell(command):
    print(f"\n> {command}")
    result = subprocess.run(command, shell=True, stdout=sys.stdout, stderr=sys.stderr)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {result.returncode}")
    return ""


usage = """
usage: %prog [options]
Tears down shared infrastructure created by infra-up.py.

Deletes:
- OIDC config
- Account roles (shared prefix)
- VPC and subnets (via Terraform)

Will refuse if clusters still exist unless --force is used.
"""

parser = OptionParser(usage=usage)
parser.add_option("-f", "--infra-file", dest="infra_file", help="Path to infra state file (from infra-up.py)")
parser.add_option("-p", "--profile", dest="profile", default=None, help="AWS profile to use")
parser.add_option("--force", dest="force", action="store_true", default=False, help="Skip cluster check and force teardown")

(options, args) = parser.parse_args()

infra_file = options.infra_file

if not infra_file:
    parser.print_help()
    exit(0)

if options.profile:
    os.environ["AWS_PROFILE"] = options.profile

script_dir = os.path.dirname(os.path.abspath(__file__))

# Load infra state
with open(infra_file) as f:
    infra = json.load(f)

env_name = infra["name"]
oidc_config_id = infra["oidc_config_id"]
account_role_prefix = infra["account_role_prefix"]
clusters = infra.get("clusters", [])

# Check for remaining clusters
if clusters and not options.force:
    print(f"Error: {len(clusters)} cluster(s) still exist: {', '.join(clusters)}")
    print("Tear down all clusters first with ./down.py, or use --force to skip this check.")
    exit(1)

if clusters and options.force:
    print(f"WARNING: Force mode - skipping check for {len(clusters)} remaining cluster(s): {', '.join(clusters)}")

# Delete OIDC config
print("Deleting OIDC config...")
try:
    shell(f"rosa delete oidc-config --oidc-config-id={oidc_config_id} --mode=auto --yes")
except RuntimeError:
    print("OIDC config already deleted or not found.")

# Delete account roles
print("Deleting account roles...")
try:
    shell(f"rosa delete account-roles --prefix={account_role_prefix} --mode=auto --yes")
except RuntimeError:
    print("Account roles already deleted or not found.")

# Clean up orphaned VPC resources not managed by Terraform
vpc_id = infra.get("vpc_id", "")
region = infra.get("region", "us-east-1")
profile_flag = f"--profile {options.profile}" if options.profile else ""

if vpc_id:
    print(f"\nWARNING: Checking for orphaned resources in VPC {vpc_id} not managed by Terraform.")
    print("These may have been created by ROSA or Karpenter and left behind after cluster deletion.")

    # Check if there are any orphaned resources
    orphaned_sgs = json.loads(
        subprocess.run(
            f"aws ec2 describe-security-groups --filters Name=vpc-id,Values={vpc_id} "
            f"--query \"SecurityGroups[?GroupName!='default'].GroupId\" --output json "
            f"--region {region} {profile_flag}",
            shell=True, capture_output=True, text=True
        ).stdout or "[]"
    )
    orphaned_enis = json.loads(
        subprocess.run(
            f"aws ec2 describe-network-interfaces --filters Name=vpc-id,Values={vpc_id} "
            f"--query \"NetworkInterfaces[].NetworkInterfaceId\" --output json "
            f"--region {region} {profile_flag}",
            shell=True, capture_output=True, text=True
        ).stdout or "[]"
    )
    orphaned_igws = json.loads(
        subprocess.run(
            f"aws ec2 describe-internet-gateways --filters Name=attachment.vpc-id,Values={vpc_id} "
            f"--query \"InternetGateways[].InternetGatewayId\" --output json "
            f"--region {region} {profile_flag}",
            shell=True, capture_output=True, text=True
        ).stdout or "[]"
    )
    orphaned_subnets = json.loads(
        subprocess.run(
            f"aws ec2 describe-subnets --filters Name=vpc-id,Values={vpc_id} "
            f"--query \"Subnets[].SubnetId\" --output json "
            f"--region {region} {profile_flag}",
            shell=True, capture_output=True, text=True
        ).stdout or "[]"
    )

    has_orphans = orphaned_sgs or orphaned_enis or orphaned_igws or orphaned_subnets

    if has_orphans:
        print("\nFound orphaned resources:")
        for sg in orphaned_sgs:
            print(f"  Security Group: {sg}")
        for eni in orphaned_enis:
            print(f"  Network Interface: {eni}")
        for igw in orphaned_igws:
            print(f"  Internet Gateway: {igw}")
        for subnet in orphaned_subnets:
            print(f"  Subnet: {subnet}")

        confirm = input("\nDelete these orphaned resources? (y/N): ").strip().lower()
        if confirm != "y":
            print("Skipping orphan cleanup. Terraform may fail if these block VPC deletion.")
        else:
            for sg in orphaned_sgs:
                print(f"  Deleting security group: {sg}")
                subprocess.run(
                    f"aws ec2 delete-security-group --group-id {sg} --region {region} {profile_flag}",
                    shell=True, stdout=sys.stdout, stderr=sys.stderr
                )
            for eni in orphaned_enis:
                print(f"  Deleting ENI: {eni}")
                subprocess.run(
                    f"aws ec2 delete-network-interface --network-interface-id {eni} --region {region} {profile_flag}",
                    shell=True, stdout=sys.stdout, stderr=sys.stderr
                )
            for igw in orphaned_igws:
                print(f"  Detaching and deleting IGW: {igw}")
                subprocess.run(
                    f"aws ec2 detach-internet-gateway --internet-gateway-id {igw} --vpc-id {vpc_id} "
                    f"--region {region} {profile_flag}",
                    shell=True, stdout=sys.stdout, stderr=sys.stderr
                )
                subprocess.run(
                    f"aws ec2 delete-internet-gateway --internet-gateway-id {igw} --region {region} {profile_flag}",
                    shell=True, stdout=sys.stdout, stderr=sys.stderr
                )
            for subnet in orphaned_subnets:
                print(f"  Deleting subnet: {subnet}")
                subprocess.run(
                    f"aws ec2 delete-subnet --subnet-id {subnet} --region {region} {profile_flag}",
                    shell=True, stdout=sys.stdout, stderr=sys.stderr
                )
    else:
        print("No orphaned resources found.")

# Destroy Terraform infrastructure
print("Destroying Terraform infrastructure...")
tf_state_file = os.path.join(script_dir, f"infra-{env_name}.tfstate")

shell(f"terraform -chdir={script_dir} init")
shell(f"terraform -chdir={script_dir} destroy -auto-approve -state={tf_state_file}")

# Clean up state files
os.remove(infra_file)
print(f"Removed {infra_file}")

if os.path.exists(tf_state_file):
    os.remove(tf_state_file)
    print(f"Removed {tf_state_file}")

tf_state_backup = tf_state_file + ".backup"
if os.path.exists(tf_state_backup):
    os.remove(tf_state_backup)
    print(f"Removed {tf_state_backup}")

print(f"\nShared infrastructure for '{env_name}' destroyed successfully.")
