# Operator runbook

1. Use an owner-approved disposable OCP 4.20 lab first. Confirm the source is a single healthy control-plane node and provide its hostname only in an external inventory.
2. Install with `backup_install_confirm=true`. The timer is UTC `00,06,12,18`, has no implicit catch-up, and uses `flock` to reject overlap.
3. Set root-only `OCP_ETCD_BACKUP_VERSION` and `OCP_ETCD_BACKUP_CLUSTER_ID` in the service environment from verified cluster facts. Do not store those values in Git.
4. List candidates with `playbooks/list-backups.yml`; select an explicit `set-YYYYMMDDTHHMMSSZ`. Never choose “latest” implicitly.
5. Run restore preflight. It is read-only and must pass for every inventory control-plane host before any mutation.
6. For an owner-authorized lab restore only, invoke apply with the exact `RESTORE_<cluster-id>` confirmation. Capture the controller output and preserve the selected source set.
7. If fencing, copy, or vendor restore reports an error, stop. Do not rerun the destructive phase blindly. Escalate using the checkpoint and vendor logs.

The role does not activate schedules during check mode. Backup failures preserve existing complete sets; investigate the vendor log and staging evidence. A local schedule does not guarantee a six-hour RPO when a run fails.
