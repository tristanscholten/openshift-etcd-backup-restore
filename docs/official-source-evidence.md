# Official source evidence

The implementation calls installed vendor scripts; it does not download or embed vendor recovery logic.

- Red Hat OCP 4.20 etcd guide: https://docs.redhat.com/en/documentation/openshift_container_platform/4.20/html/etcd/backing-up-and-restoring-etcd-data
- PDF: https://docs.redhat.com/en/documentation/openshift_container_platform/4.20/pdf/etcd/OpenShift_Container_Platform-4.20-etcd-en-US.pdf
- `cluster-backup.sh`: https://github.com/openshift/cluster-etcd-operator/blob/release-4.20/bindata/etcd/cluster-backup.sh
- `cluster-restore.sh`: https://github.com/openshift/cluster-etcd-operator/blob/release-4.20/bindata/etcd/cluster-restore.sh
- Reviewed release-4.20 restore source commit: `72555433bbf7bdb9ce5f8d93c3f49375c2770f0a`

Mapping: backup calls `cluster-backup.sh` once from one control-plane host. SNO invokes `cluster-restore.sh` on the recovery host with `ETCD_ETCDCTL_RESTORE` unset. HA validates the pair once on the source, copies and revalidates it on the recovery host, fences all control-plane hosts with `disable-etcd.sh`, and calls `cluster-restore.sh` once. Only after API recovery, an owner-supplied certificate kubeconfig and prevalidated two-machine mapping authorize the documented quorum-guard patch, sequential Machine deletion/recreation, stability wait, and guard removal. The implementation has not been run in an owner-approved OCP 4.20 lab; this is test-harness evidence, not production DR validation.
