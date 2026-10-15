# HA backup identity capture checkpoint

Scope: backup-time capture only. This checkpoint adds no restore mutation and does not authorize a cluster action.

## Runtime interface

The scheduled root service receives these root-only EnvironmentFile values:

- `OCP_ETCD_BACKUP_TOPOLOGY`: `sno` or `ha`.
- `OCP_ETCD_BACKUP_CAPTURE_KUBECONFIG`: absolute, readable regular file holding the authorized healthy-cluster capture credential.
- `OCP_ETCD_BACKUP_OC`: absolute `oc` executable path.
- `OCP_ETCD_BACKUP_HA_CAPTURE_CONFIG`: `/etc/ocp-etcd-backup-ha-capture.json`, mode 0600.

For HA, the JSON configuration contains exactly three entries in `control_plane_hosts`, each with `inventory_hostname`, approved stable non-secret `ssh_host_identity`, and expected `node_name`, plus `recovery_host`. The approved SSH bootstrap process must collect the platform's stable host identity before installation and supply the same value here; it must not use an IP address, a Machine name, or `/etc/machine-id` alone. The helper makes only read-only `oc get` calls while the original API is healthy. It never logs the kubeconfig, resource bodies, or credentials.

## Persisted interface

A newly promoted HA set is metadata schema 2. Its `pair_payload_sha256` is SHA-256 of canonical JSON containing only the set ID, OCP version, cluster ID, two payload names, and their SHA-256 values. This non-circular digest is copied into `capture.pair_payload_sha256`.

`machine-recovery-inventory-v1.json` is mode 0600 and its SHA-256 appears both in metadata and `SHA256SUMS`. It records only schema/capture fields; Node name/UID/providerID; exact non-recovery Machine name/UID/NodeRef/providerID; and either Active three-replica CPMS v1 evidence or observed CPMS absence. Machine eligibility is proven from Machine `status.nodeRef` plus Machine `spec.providerID` matching the exact Node, not a platform-label string.

Older schema-1 sets remain discoverable. Schema-2 SNO sets have no HA inventory. Schema-2 HA validation rejects missing, malformed, duplicate, mismatched, non-0600, non-regular, unbound, or unhashed inventory.

## Stage 2 input

Stage 2 must read the selected validated schema-2 HA set only. It must derive every non-recovery host-to-Machine mapping from this record, validate the stored pair digest and all exact identities offline before mutation, and perform fresh read-only post-recovery reconciliation before any Machine deletion. It must not accept an extra-vars mapping override.

## Evidence

OpenShift documentation consulted through Context7: Machine API `machine.openshift.io/v1beta1` list endpoint; CPMS `machine.openshift.io/v1` with `spec.state: Active` and three replicas. This implementation remains a simulated-fixture candidate, not OCP DR-lab proof.
