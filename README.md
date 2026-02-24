# Tools

Collection of automation scripts for ROSA cluster management and testing.

## [hcp-cluster-automation](hcp-cluster-automation/)

Create and tear down ROSA HCP (Hosted Control Plane) clusters with a single command. Supports standard public clusters, zero-egress (private/air-gapped) clusters, and AutoNode (Karpenter) autoscaling.

```bash
./up.py -c my-cluster -r us-east-1         # Standard HCP
./up.py -c my-cluster -r us-west-2 -z      # Zero-egress
./up.py -c my-cluster -a                    # With AutoNode
./down.py -c my-cluster -x                  # Tear down
```
