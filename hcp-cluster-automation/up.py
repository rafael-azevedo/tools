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
Creates a ROSA HCP cluster on existing shared infrastructure.

Inputs:
- Cluster name
- Infra state file (from infra-up.py)

Outputs:
- Operator roles for the cluster
- A ROSA HCP cluster on the shared VPC
- Per-cluster state file: cluster-<name>.json
"""

parser = OptionParser(usage=usage)
parser.add_option("-c", "--cluster-name", dest="name", help="Cluster name (max 15 chars, lowercase alphanumeric + hyphens)")
parser.add_option("-f", "--infra-file", dest="infra_file", help="Path to infra state file (from infra-up.py)")
parser.add_option("-v", "--version", dest="version", default=None, help="OpenShift version (default: latest available)")
parser.add_option("-p", "--profile", dest="profile", default=None, help="AWS profile to use")
parser.add_option("-a", "--autonode", dest="autonode", action="store_true", default=False, help="Enable AutoNode (Karpenter) on the cluster")

(options, args) = parser.parse_args()

try:
    cluster_name = options.name
    infra_file = options.infra_file
    version = options.version
    profile = options.profile
    autonode = options.autonode

    if not cluster_name or not infra_file:
        parser.print_help()
        exit(0)

    if profile:
        os.environ["AWS_PROFILE"] = profile

    script_dir = os.path.dirname(os.path.abspath(__file__))

    # Load infra state
    with open(infra_file) as f:
        infra = json.load(f)

    env_name = infra["name"]
    region = infra["region"]
    account_id = infra["account_id"]
    oidc_config_id = infra["oidc_config_id"]
    account_role_prefix = infra["account_role_prefix"]
    private_subnet_ids = infra["private_subnet_ids"]
    public_subnet_ids = infra["public_subnet_ids"]
    billing_account = infra.get("billing_account", "")
    zero_egress = infra.get("zero_egress", False)
    vpc_id = infra["vpc_id"]

    if cluster_name in infra.get("clusters", []):
        print(f"Error: Cluster '{cluster_name}' already exists in {infra_file}.")
        exit(1)

    cluster_state_file = os.path.join(script_dir, f"cluster-{cluster_name}.json")
    if os.path.exists(cluster_state_file):
        print(f"Error: Cluster state file {cluster_state_file} already exists.")
        exit(1)

    # Zero-egress: attach ECR ReadOnly policy to shared worker role
    if zero_egress:
        shell(
            f"aws iam attach-role-policy --role-name {account_role_prefix}-HCP-ROSA-Worker-Role "
            f"--policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
        )

    # Create operator roles for this cluster (using shared OIDC config)
    shell(
        f"rosa create operator-roles --mode=auto --yes --hosted-cp "
        f"--prefix={cluster_name} --oidc-config-id={oidc_config_id} "
        f"--installer-role-arn arn:aws:iam::{account_id}:role/{account_role_prefix}-HCP-ROSA-Installer-Role"
    )

    # If autonode, get tech-preview shard info and override region
    shard_props = ""
    if autonode:
        print("AutoNode enabled - getting tech-preview shard info...")
        shard_json = shell('ocm get /api/osd_fleet_mgmt/v1/service_clusters -p "search=sector=\'tech-preview\'"')
        shard_data = json.loads(shard_json)
        if not shard_data.get("items"):
            raise RuntimeError("No tech-preview shard found")
        shard = shard_data["items"][0]
        shard_id = shard["provision_shard_reference"]["id"]
        shard_region = shard["region"]
        region = shard_region  # override region to match shard
        print(f"Using tech-preview shard: {shard_id} in {shard_region}")
        shard_props = f"--properties provision_shard_id:{shard_id} "

    # Build subnet list
    if zero_egress:
        subnet_ids = ",".join(private_subnet_ids)
    else:
        subnet_ids = ",".join(public_subnet_ids + private_subnet_ids)

    # Create the cluster
    shell(
        f"rosa create cluster --cluster-name {cluster_name} --sts --yes --mode auto "
        f"--role-arn arn:aws:iam::{account_id}:role/{account_role_prefix}-HCP-ROSA-Installer-Role "
        f"--support-role-arn arn:aws:iam::{account_id}:role/{account_role_prefix}-HCP-ROSA-Support-Role "
        f"--worker-iam-role arn:aws:iam::{account_id}:role/{account_role_prefix}-HCP-ROSA-Worker-Role "
        f"--operator-roles-prefix {cluster_name} "
        f"--oidc-config-id {oidc_config_id} "
        f"--region {region} "
        f"{'--billing-account ' + billing_account + ' ' if billing_account else ''}"
        f"{'--version ' + version + ' ' if version else ''}"
        f"--replicas 2 "
        f"--compute-machine-type m5.xlarge "
        f"--machine-cidr 10.0.0.0/16 "
        f"--service-cidr 172.30.0.0/16 "
        f"--pod-cidr 10.128.0.0/14 "
        f"--host-prefix 23 "
        f"--subnet-ids {subnet_ids} "
        f"{'--private --default-ingress-private ' if zero_egress else ''}"
        f"{shard_props}"
        f"{'--properties zero_egress:true ' if zero_egress else ''}"
        f"--hosted-cp"
    )

    # Per-cluster state
    cluster_state = {
        "cluster_name": cluster_name,
        "infra_file": os.path.basename(infra_file),
        "env_name": env_name,
        "region": region,
        "account_id": account_id,
        "oidc_config_id": oidc_config_id,
        "operator_role_prefix": cluster_name,
        "account_role_prefix": account_role_prefix,
        "zero_egress": zero_egress,
        "autonode": autonode,
        "autonode_role_arn": "",
    }

    if autonode:
        # Wait for cluster to be ready
        print("Waiting for cluster to be ready...")
        shell(f"rosa logs install --cluster={cluster_name} --watch")

        # Get cluster ID
        cluster_json = shell(f"rosa describe cluster --cluster={cluster_name} -o json")
        cluster_id = json.loads(cluster_json)["id"]

        # Create AutoNode IAM policy
        print("Creating AutoNode IAM policy...")
        policy_file = os.path.join(script_dir, "autonode-policy.json")
        policy_arn = shell(
            f"aws iam create-policy --policy-name {cluster_name}-autonode "
            f"--policy-document file://{policy_file} "
            f"--query 'Policy.Arn' --output text"
        ).strip()

        # Get OIDC provider URL
        oidc_list = shell("rosa list oidc-config -o json")
        oidc_provider_url = None
        for oidc in json.loads(oidc_list):
            if oidc["id"] == oidc_config_id:
                oidc_provider_url = oidc["issuer_url"].replace("https://", "")
                break

        if not oidc_provider_url:
            raise RuntimeError(f"Could not find OIDC provider URL for config {oidc_config_id}")

        # Create trust policy
        trust_policy = json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {
                    "Federated": f"arn:aws:iam::{account_id}:oidc-provider/{oidc_provider_url}"
                },
                "Action": "sts:AssumeRoleWithWebIdentity",
                "Condition": {
                    "StringEquals": {
                        f"{oidc_provider_url}:sub": "system:serviceaccount:kube-system:karpenter"
                    }
                }
            }]
        })

        trust_policy_file = os.path.join(script_dir, f"{cluster_name}-trust-policy.json")
        with open(trust_policy_file, "w") as f:
            f.write(trust_policy)

        # Create IAM role and attach policy
        print("Creating AutoNode IAM role...")
        role_name = f"{cluster_name}-autonode-operator-role"
        shell(f"aws iam create-role --role-name {role_name} --assume-role-policy-document file://{trust_policy_file}")
        shell(f"aws iam attach-role-policy --role-name {role_name} --policy-arn {policy_arn}")

        role_arn = f"arn:aws:iam::{account_id}:role/{role_name}"
        cluster_state["autonode_role_arn"] = role_arn

        # Enable AutoNode via OCM patch
        print("Enabling AutoNode on cluster...")
        patch_body = json.dumps({
            "auto_node": {"mode": "enabled"},
            "aws": {"auto_node": {"role_arn": role_arn}}
        })

        patch_file = os.path.join(script_dir, f"{cluster_name}-patch-autonode.json")
        with open(patch_file, "w") as f:
            f.write(patch_body)

        shell(f"ocm patch /api/clusters_mgmt/v1/clusters/{cluster_id} --body {patch_file}")

        # Tag subnets and security groups for Karpenter discovery
        print("Tagging subnets and security groups for Karpenter...")
        os.environ["AWS_REGION"] = region

        # Find the default security group for the VPC
        sg_id = shell(
            f'aws ec2 describe-security-groups --filters '
            f'"Name=vpc-id,Values={vpc_id}" "Name=group-name,Values=default" '
            f'--query "SecurityGroups[0].GroupId" --output text'
        ).strip()

        private_subnet = private_subnet_ids[0]

        resources = []
        if sg_id and sg_id != "None":
            resources.append(sg_id)
        if private_subnet and private_subnet != "None":
            resources.append(private_subnet)

        if resources:
            shell(
                f'aws ec2 create-tags --resources {" ".join(resources)} '
                f'--tags Key=karpenter.sh/discovery,Value={cluster_id}'
            )
        else:
            print("WARNING: Could not find security group or subnet to tag for Karpenter.")
            print(f"Manually tag resources with: karpenter.sh/discovery={cluster_id}")

        print(f"\nAutoNode enabled on cluster '{cluster_name}' (ID: {cluster_id})")
        print(f"AutoNode role ARN: {role_arn}")
        print("Default OpenshiftEC2NodeClass should be created automatically.")
        print("Create a NodePool to start using AutoNode - see docs for examples.")

    # Write per-cluster state
    with open(cluster_state_file, "w") as f:
        json.dump(cluster_state, f, indent=2)

    # Update infra state - add cluster to list
    infra["clusters"].append(cluster_name)
    with open(infra_file, "w") as f:
        json.dump(infra, f, indent=2)

    print(f"\nCluster '{cluster_name}' created successfully on environment '{env_name}'.")
    print(f"Cluster state: {cluster_state_file}")
    print(f"\nTo tear down: ./down.py -c {cluster_name} -f {infra_file}")

except Exception as e:
    print(f"Error: {e}")
    print(
        f"Run ./down.py -c {cluster_name} -f {infra_file} to clean up before retrying."
    )
    exit(1)
