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
Creates shared infrastructure for ROSA HCP clusters.

Inputs:
- Environment name
- AWS region

Outputs:
- A new VPC with public and private subnets (via Terraform)
- A shared OIDC config (reusable across clusters)
- Shared account roles (prefixed with env name)
- State file: infra-<name>.json
"""

parser = OptionParser(usage=usage)
parser.add_option("-n", "--name", dest="name", help="Environment name (max 15 chars, lowercase alphanumeric + hyphens)")
parser.add_option("-r", "--region", dest="region", default="us-east-1", help="AWS region (default: us-east-1)")
parser.add_option("-p", "--profile", dest="profile", default=None, help="AWS profile to use")
parser.add_option("-z", "--zero-egress", dest="zero_egress", action="store_true", default=False, help="Create zero-egress VPC (private, no internet)")
parser.add_option("-b", "--billing-account", dest="billing_account", default=None, help="AWS billing account ID (default: infrastructure account)")

(options, args) = parser.parse_args()

try:
    env_name = options.name
    region = options.region
    profile = options.profile
    zero_egress = options.zero_egress
    billing_account = options.billing_account

    if not env_name:
        parser.print_help()
        exit(0)

    if profile:
        os.environ["AWS_PROFILE"] = profile

    script_dir = os.path.dirname(os.path.abspath(__file__))
    state_file = os.path.join(script_dir, f"infra-{env_name}.json")

    if os.path.exists(state_file):
        print(f"Error: State file {state_file} already exists.")
        print(f"Environment '{env_name}' appears to already be provisioned.")
        print(f"Use infra-down.py to tear it down first, or choose a different name.")
        exit(1)

    # Get AWS account ID
    whoami = json.loads(shell("rosa whoami -ojson"))
    account_id = whoami["AWS Account ID"]

    # Create account roles (shared across clusters in this env)
    shell(
        f"rosa create account-roles --mode=auto --yes --hosted-cp --prefix {env_name}"
    )

    # Create OIDC config (shared across clusters in this env)
    oidc_json = shell("rosa create oidc-config --mode=auto --yes -o json")
    oidc_config_id = json.loads(oidc_json)["id"]

    # Create VPC via Terraform
    tf_state_file = os.path.join(script_dir, f"infra-{env_name}.tfstate")

    shell(f"terraform -chdir={script_dir} init")
    shell(
        f"terraform -chdir={script_dir} plan -out rosa.tfplan "
        f"-state={tf_state_file} "
        f"-var 'env_name={env_name}' "
        f"-var 'region={region}' "
        f"-var 'vpc_cidr_block=10.0.0.0/16' "
        f"-var 'private_subnets=[\"10.0.0.0/24\"]' "
        f"-var 'public_subnets=[\"10.0.128.0/24\"]' "
        f"-var 'availability_zones=[\"{region}a\"]' "
        f"-var 'zero_egress={str(zero_egress).lower()}'"
    )
    shell(f'terraform -chdir={script_dir} apply -state={tf_state_file} "rosa.tfplan"')

    terraform_out = shell(f"terraform -chdir={script_dir} output -state={tf_state_file} -json")
    tf_output = json.loads(terraform_out)

    vpc_id = tf_output["vpc_id"]["value"]
    private_subnet_ids = json.loads(tf_output["private_subnet_ids"]["value"])
    public_subnet_ids = json.loads(tf_output["public_subnet_ids"]["value"])

    # Write state file
    state = {
        "name": env_name,
        "region": region,
        "account_id": account_id,
        "vpc_id": vpc_id,
        "private_subnet_ids": private_subnet_ids,
        "public_subnet_ids": public_subnet_ids,
        "oidc_config_id": oidc_config_id,
        "account_role_prefix": env_name,
        "billing_account": billing_account or "",
        "zero_egress": zero_egress,
        "clusters": [],
    }

    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)

    print(f"\nShared infrastructure created successfully.")
    print(f"State file: {state_file}")
    print(f"VPC ID: {vpc_id}")
    print(f"OIDC Config ID: {oidc_config_id}")
    print(f"\nNext: ./up.py -c <cluster-name> -f {state_file}")

except Exception as e:
    print(f"Error: {e}")
    print(f"If partially created, run: ./infra-down.py -f infra-{env_name}.json --force")
    exit(1)
