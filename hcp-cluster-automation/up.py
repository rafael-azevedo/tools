#!/usr/bin/env python3

import json
import os

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
- AWS region
Outputs:
- A new VPC with public and private subnets, NAT gateway, and internet gateway
- A standard ROSA HCP cluster

Note:
- Creates a single-AZ VPC in 10.0.0.0/16. If something exists in that address space
  already in the region, the installation will fail.
"""

parser = OptionParser(usage=usage)
parser.add_option("-c", "--cluster-name", dest="name", help="Hosted cluster name")
parser.add_option("-r", "--region", dest="region", default="us-east-1", help="Region (default: us-east-1)")
parser.add_option("-v", "--version", dest="version", default=None, help="OpenShift version (default: latest available)")
parser.add_option("-p", "--profile", dest="profile", default=None, help="AWS profile to use")
parser.add_option("-b", "--billing-account", dest="billing_account", default=None, help="AWS billing account ID (default: uses infrastructure account)")
parser.add_option("-a", "--autonode", dest="autonode", action="store_true", default=False, help="Enable AutoNode (Karpenter) on the cluster")
parser.add_option("-z", "--zero-egress", dest="zero_egress", action="store_true", default=False, help="Create a zero-egress (private, no internet) cluster")

(options, args) = parser.parse_args()

try:
    cluster_name = options.name
    region = options.region
    version = options.version
    billing_account = options.billing_account
    profile = options.profile

    autonode = options.autonode
    zero_egress = options.zero_egress

    if not cluster_name:
        parser.print_help()
        exit(0)

    if profile:
        os.environ["AWS_PROFILE"] = profile

    script_dir = os.path.dirname(os.path.abspath(__file__))

    whoami = json.loads(shell("rosa whoami -ojson"))
    account_id = whoami["AWS Account ID"]

    shell(
        f"rosa create account-roles --mode=auto --yes --hosted-cp --prefix {cluster_name}"
    )

    oidc_json = shell(f"rosa create oidc-config --mode=auto --yes -o json")
    oidc_config_id = json.loads(oidc_json)["id"]

    if zero_egress:
        shell(
            f"aws iam attach-role-policy --role-name {cluster_name}-HCP-ROSA-Worker-Role "
            f"--policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
        )

    shell(
        f"rosa create operator-roles --mode=auto --yes --hosted-cp "
        f"--prefix={cluster_name} --oidc-config-id={oidc_config_id} "
        f"--installer-role-arn arn:aws:iam::{account_id}:role/{cluster_name}-HCP-ROSA-Installer-Role"
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

    shell(f"terraform -chdir={script_dir} init")
    shell(
        f"terraform -chdir={script_dir} plan -out rosa.tfplan "
        f"-var 'cluster_name={cluster_name}' "
        f"-var 'region={region}' "
        f"-var 'vpc_cidr_block=10.0.0.0/16' "
        f"-var 'private_subnets=[\"10.0.0.0/24\"]' "
        f"-var 'public_subnets=[\"10.0.128.0/24\"]' "
        f"-var 'availability_zones=[\"{region}a\"]' "
        f"-var 'zero_egress={str(zero_egress).lower()}'"
    )
    shell(f'terraform -chdir={script_dir} apply "rosa.tfplan"')

    terraform_out = shell(f"terraform -chdir={script_dir} output -json")
    tf_output = json.loads(terraform_out)

    private_ids = json.loads(tf_output["private_subnet_ids"]["value"])
    if zero_egress:
        subnet_ids = ",".join(private_ids)
    else:
        public_ids = json.loads(tf_output["public_subnet_ids"]["value"])
        subnet_ids = ",".join(public_ids + private_ids)

    shell(
        f"rosa create cluster --cluster-name {cluster_name} --sts --yes --mode auto "
        f"--role-arn arn:aws:iam::{account_id}:role/{cluster_name}-HCP-ROSA-Installer-Role "
        f"--support-role-arn arn:aws:iam::{account_id}:role/{cluster_name}-HCP-ROSA-Support-Role "
        f"--worker-iam-role arn:aws:iam::{account_id}:role/{cluster_name}-HCP-ROSA-Worker-Role "
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

        # Get VPC ID from terraform
        vpc_id = tf_output["vpc_id"]["value"]

        # Find the default security group for the VPC
        sg_id = shell(
            f'aws ec2 describe-security-groups --filters '
            f'"Name=vpc-id,Values={vpc_id}" "Name=group-name,Values=default" '
            f'--query "SecurityGroups[0].GroupId" --output text'
        ).strip()

        # Use the private subnet from terraform output
        private_subnet = private_ids[0]

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

except Exception as e:
    print(f"Error: {e}")
    print(
        f"Run ./down.py -c {cluster_name} to remove old operator roles, oidc config and account roles before retrying."
    )
    exit(1)
