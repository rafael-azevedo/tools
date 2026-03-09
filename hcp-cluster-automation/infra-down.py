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
