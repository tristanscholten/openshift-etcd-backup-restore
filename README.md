# OCP etcd backup and restore

Status: custom scheduler/test-harness implementation only. No real OpenShift cluster or DR exercise has been run. Do not use this repository on a customer, HSTR, CRC, or production cluster without owner-approved validation.

## Primary quick start: CronJob manifest

The primary backup method is [`manifests/etcd-backup-cronjob.yaml`](manifests/etcd-backup-cronjob.yaml), not the legacy Ansible timer. It schedules the installed OpenShift control-plane `/usr/local/bin/cluster-backup.sh` every six hours at `0 */6 * * *` UTC on exactly one selected control-plane node.

1. Replace `REPLACE_WITH_ONE_CONTROL_PLANE_HOSTNAME` with the output hostname of one control-plane node. Do not select every control-plane node.
2. Review the elevated access warning below, then apply and inspect:

   ```sh
   oc apply -f manifests/etcd-backup-cronjob.yaml
   oc -n etcd-backup-system get cronjob,job
   oc -n etcd-backup-system describe cronjob/etcd-backup
   ```

3. Optionally run it once without changing the schedule:

   ```sh
   oc -n etcd-backup-system create job --from=cronjob/etcd-backup etcd-backup-manual-$(date -u +%Y%m%d%H%M%S)
   oc -n etcd-backup-system logs -f job/etcd-backup-manual-REPLACE_WITH_CREATED_NAME
   ```

The vendor pair is stored node-locally at `/home/core/etcd-backups`, mode `0700`. The CronJob marks only pairs it created and, only after a new vendor-successful complete `snapshot_<timestamp>.db` plus `static_kuberesources_<timestamp>.tar.gz` pair, retains the newest three marked complete pairs. It never broadly removes unmarked/pre-existing files. A failed or partial vendor invocation does not prune prior marked pairs.

To uninstall the schedule and its namespace resources without deleting stored host backup pairs:

```sh
oc delete -f manifests/etcd-backup-cronjob.yaml
```

Do not run the legacy timer and this CronJob at the same time. Existing timer removal is optional legacy cleanup, not a prerequisite for this manifest:

```sh
ansible-playbook -i inventory/hosts.yml playbooks/uninstall.yml \
  -e backup_source_hostname=SOURCE -e backup_uninstall_confirm=true
```

## Elevated host access and support boundary

This manifest creates a dedicated service account with a narrowly scoped ClusterRole that can use only OpenShift's existing `privileged` SCC. Its pod is privileged, uses the selected node's network namespace, and mounts that node's `/` at `/host`, then calls `chroot /host /usr/local/bin/cluster-backup.sh /home/core/etcd-backups`. Host networking is required because the installed vendor script uses the host kubeconfig and local etcd endpoint. This is highly privileged host-root and host-network access: review and authorize it accordingly. The pod does not use host PID or IPC, does not mount a service-account token, and receives no cluster-admin role.

The vendor script is the OpenShift-supported backup mechanism; this CronJob and its marker-only retention loop are custom scheduling glue. The verified baseline is OCP 4.20 documentation source at commit `81db4e1af3dd2c203315a42f2790d64eafd33c80`; no compatibility is claimed for later OCP releases. If the cluster-wide proxy is enabled, configure the required `HTTP_PROXY`, `HTTPS_PROXY`, and `NO_PROXY` environment variables according to the official documentation before relying on this job; this repository does not automate proxy configuration.

OCP 4.20 also documents a native Backup CR/CronJob feature. It is Technology Preview and requires irreversible `TechPreviewNoUpgrade`, which prevents minor upgrades and must not be enabled on production clusters. This repository does not enable or use that feature.

A node-local backup is not off-host DR and does not protect PV or application data. Store the static-resource archive securely: it can contain etcd encryption keys.

## Restore

On a total control-plane outage, restore needs no old backup-role installation or helper. The CronJob writes a minimal root-private manifest beside each ordinary vendor pair while the healthy host API is available; it records the cluster UUID, exact desired OCP `4.20.z` release, SHA-256 values, non-CPMS topology, and each control-plane Node system UUID/current MachineConfig. Before destructive work, Ansible requires the selected manifest to bind the exact inventory and validates every SSH target's DMI system UUID and installed MachineConfig offline. This is the release identity used by recovery; an `oc` client RPM version is not authoritative.

For an owner-authorized OCP 4.20 recovery only, use one SSH Ansible invocation. It first discovers and displays complete pairs, prompts for one timestamp when `restore_selection` is omitted, validates the manifest before mutation, and then asks once for `RESTORE_<cluster-uuid>` if `destructive_confirm` is omitted:

```sh
ansible-playbook -i inventory/hosts.yml playbooks/restore.yml \
  -e backup_source_hostname=SOURCE -e recovery_host=RECOVERY \
  -e restore_topology=ha \
  -e expected_cluster_id=CLUSTER_UUID -e expected_ocp_version=4.20.Z \
  -e post_api_kubeconfig=/secure/path/kubeconfig \
  -e restore_oc_path=/absolute/path/to/oc
```

Supported paths are SNO and exactly three non-CPMS control-plane hosts. SNO copies the matching pair to the node, runs installed `cluster-restore.sh` once, then waits for cluster stability. HA disables etcd by SSH on all three hosts, copies the same pair to one recovery host, runs `cluster-restore.sh` once, waits for the API, temporarily enables the documented etcd quorum-guard override, waits for stability, and clears that override. It never deletes Machines. CPMS is refused before destructive work: its degraded-member repair has distinct official prerequisites (two healthy members) and is not a substitute for snapshot DR after quorum loss.

`restore_mode=preflight` performs discovery and validation only. No real OpenShift recovery has been exercised; disposable fixtures prove orchestration only.

## Legacy Ansible automation

The existing Ansible timer and guarded restore remain as legacy implementation/reference material so their tests and restore source are not disrupted. They are no longer the primary backup installation path. Read `docs/operator-runbook.md` and `docs/safety-and-support-boundary.md` before using legacy paths.

## Local verification

```sh
python3 -m unittest discover -s tests -v
yamllint manifests/etcd-backup-cronjob.yaml
oc apply --dry-run=client -f manifests/etcd-backup-cronjob.yaml
ansible-playbook --syntax-check -i inventory/hosts.yml playbooks/backup-install.yml
ansible-playbook --syntax-check -i inventory/hosts.yml playbooks/restore.yml
```

The manifest fixture executes the exact embedded shell against a disposable stubbed host vendor script. It proves retention and failure behavior only; it is not OpenShift validation.
