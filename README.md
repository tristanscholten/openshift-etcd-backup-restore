# OCP 4.20 etcd backup and restore

Fail-closed Ansible automation for one OCP 4.20 control-plane backup source and the documented vendor restore path.

Status: design/test-harness complete; OCP DR not validated. Do not use this repository on a customer, HSTR, CRC, or production cluster without an owner-approved disposable OCP 4.20 DR-lab validation.

## What it does

- Installs a root-owned `systemd` timer on exactly one nominated control-plane host at `00:00,06:00,12:00,18:00 UTC`.
- Calls the installed `/usr/local/bin/cluster-backup.sh` once and retains the newest three validated complete pairs.
- Lists only validated backup metadata over SSH.
- Provides a guarded restore preflight and vendor-script invocation. It never substitutes raw `etcdctl` recovery logic.

## Quick start

Use an external, host-key-verified SSH inventory; never commit credentials, kubeconfigs, addresses, or backup contents.

```sh
ansible-playbook -i inventory/hosts.yml playbooks/backup-install.yml \
  -e backup_install_confirm=true \
  -e backup_source_hostname=SOURCE \
  -e backup_ocp_version=4.20.Z \
  -e backup_cluster_id=CLUSTER_ID
ansible-playbook -i inventory/hosts.yml playbooks/list-backups.yml -e backup_source_hostname=SOURCE
ansible-playbook -i inventory/hosts.yml playbooks/restore.yml \
  -e restore_mode=preflight -e restore_selection=SET_ID \
  -e expected_cluster_id=CLUSTER_ID
```

An apply restore requires the exact confirmation shown by the preflight:

```sh
ansible-playbook -i inventory/hosts.yml playbooks/restore.yml \
  -e restore_mode=apply -e restore_selection=SET_ID \
  -e expected_cluster_id=CLUSTER_ID \
  -e destructive_confirm=RESTORE_CLUSTER_ID
```

Read `docs/operator-runbook.md` and `docs/safety-and-support-boundary.md` first.

The install writes these three values only to `/etc/ocp-etcd-backup.conf` with root-only mode `0600`; keep the command input and controller logs private. The scheduled unit loads that EnvironmentFile, so a timer cannot run with missing version or cluster identity. To remove only the managed schedule and helpers (never retained backup sets), use:

```sh
ansible-playbook -i inventory/hosts.yml playbooks/uninstall.yml \
  -e backup_source_hostname=SOURCE -e backup_uninstall_confirm=true
```

## Local verification

```sh
python3 -m unittest discover -s tests -v
tests/test_disposable_etcd_podman.sh
ansible-playbook --syntax-check -i inventory/hosts.yml playbooks/backup-install.yml
ansible-playbook --syntax-check -i inventory/hosts.yml playbooks/restore.yml
```
