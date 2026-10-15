# Official source evidence

The implementation calls installed vendor scripts; it does not download or embed vendor recovery logic.

- Red Hat OCP 4.20 etcd guide: https://docs.redhat.com/en/documentation/openshift_container_platform/4.20/html/etcd/backing-up-and-restoring-etcd-data
- PDF: https://docs.redhat.com/en/documentation/openshift_container_platform/4.20/pdf/etcd/OpenShift_Container_Platform-4.20-etcd-en-US.pdf
- `cluster-backup.sh`: https://github.com/openshift/cluster-etcd-operator/blob/release-4.20/bindata/etcd/cluster-backup.sh
- `cluster-restore.sh`: https://github.com/openshift/cluster-etcd-operator/blob/release-4.20/bindata/etcd/cluster-restore.sh
- Reviewed release-4.20 restore source commit: `72555433bbf7bdb9ce5f8d93c3f49375c2770f0a`
- Pinned OCP 4.20 documentation source reviewed for the CronJob: https://raw.githubusercontent.com/openshift/openshift-docs/81db4e1af3dd2c203315a42f2790d64eafd33c80/modules/backup-etcd.adoc
- Pinned native automated-backup feature source: https://raw.githubusercontent.com/openshift/openshift-docs/81db4e1af3dd2c203315a42f2790d64eafd33c80/modules/etcd-creating-automated-backups.adoc

The pinned backup source directs operators to use one control-plane host, `chroot /host`, and its installed `/usr/local/bin/cluster-backup.sh <directory>`; it documents the snapshot/static-resource pair and proxy environment prerequisite. The native Backup CR feature is Technology Preview, requires irreversible `TechPreviewNoUpgrade`, prevents minor updates, and is not enabled or used here. The manifest is custom scheduling glue around the vendor script, not a claim of support for a later release.

Mapping: backup calls `cluster-backup.sh` once from one control-plane host. SNO invokes `cluster-restore.sh` on the recovery host with `ETCD_ETCDCTL_RESTORE` unset. HA validates the pair once on the source, copies and revalidates it on the recovery host, fences all control-plane hosts with `disable-etcd.sh`, and calls `cluster-restore.sh` once. Only after API recovery, an owner-supplied certificate kubeconfig and prevalidated two-machine mapping authorize the documented quorum-guard patch, sequential Machine deletion/recreation, stability wait, and guard removal. The implementation has not been run in an owner-approved OCP 4.20 lab; this is test-harness evidence, not production DR validation.
