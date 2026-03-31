# AutoNode / Karpenter Example Manifests

Before AutoNode/Karpenter is GA, you can apply these manifests to finish cluster configuration and test autoscaling after creating a cluster with `up.py -a`.

## Manifests

### nodepool.yaml

Example Karpenter NodePool that provisions on-demand or spot instances. Includes the required `NotIn` exclusion for blocked instance sizes (nano, micro, small, medium) — without this, the VAP guardrails will reject the NodePool.

```bash
oc apply -f k8s/nodepool.yaml
```

### stress-test.yaml

Creates a namespace and deployment that generates CPU load on AutoNode-provisioned nodes. Uses a `nodeSelector` targeting `autonode: "true"` to ensure pods land on Karpenter-managed nodes. Useful for verifying that Karpenter scales up nodes in response to pending pods.

```bash
oc apply -f k8s/stress-test.yaml

# Scale up to trigger more nodes
oc scale deployment stress-load -n autonode-test --replicas=10

# Clean up
oc delete namespace autonode-test
```

### log-forwarder-s3.json (optional)

ClusterLogForwarder configuration that sends logs to an S3 bucket. Log forwarding is not necessary for AutoNode testing. If you do want it, replace `<your-s3-bucket-name>` with your actual bucket name before applying.

### s3-bucket-policy.json (optional)

IAM bucket policy for the S3 log destination. Replace `<your-s3-bucket-name>` and `<log-distributor-account-id>` with your values. Apply in AWS before configuring the log forwarder.

To keep your real values locally without committing them, copy to `.local.json` files (gitignored):

```bash
cp k8s/log-forwarder-s3.json k8s/log-forwarder-s3.local.json
cp k8s/s3-bucket-policy.json k8s/s3-bucket-policy.local.json
# Edit the .local.json files with your values
```
