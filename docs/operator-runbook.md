# Operator runbook

1. Use an owner-approved disposable OCP 4.20 lab first. Confirm the source is a single healthy control-plane node and provide its hostname only in an external inventory.
2. Install with `backup_install_confirm=true`. The timer is UTC `00,06,12,18`, has no implicit catch-up, and uses `flock` to reject overlap.
3. Set root-only `OCP_ETCD_BACKUP_VERSION` and `OCP_ETCD_BACKUP_CLUSTER_ID` in the service environment from verified cluster facts. Do not store those values in Git.
4. List candidates with `playbooks/list-backups.yml`; select an explicit `set-YYYYMMDDTHHMMSSZ`. Never choose “latest” implicitly.
5. Run restore preflight. It is read-only and must pass for every inventory control-plane host before any mutation.
6. For an owner-authorized lab restore only, invoke apply with the exact `RESTORE_<cluster-id>` confirmation. Capture the controller output and preserve the selected source set.
7. This legacy HA timer/runbook predates the CronJob restore path and is not the supported recovery procedure. For the supported OCP 4.20 CronJob pair flow, use `playbooks/restore.yml`: it validates the selected manifest's non-CPMS topology, exact inventory, DMI system UUIDs, and current MachineConfigs offline before any fencing; it then copies/re-hashes the pair, fences all hosts, restores once, waits for API/stability, and clears the temporary quorum guard only after successful stability. It never deletes Machines.

The role does not activate schedules during check mode. Backup failures preserve existing complete sets; investigate the vendor log and staging evidence. A local schedule does not guarantee a six-hour RPO when a run fails.
