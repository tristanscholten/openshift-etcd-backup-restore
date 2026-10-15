# Safety and support boundary

Supported baseline is OCP `4.20.z` only, where every control-plane host and the selected set match exactly. The tool rejects unknown minors, mixed versions, invalid hashes, unsafe paths, incomplete pairs, unsupported control-plane counts, incomplete inventory, and a missing explicit confirmation.

Do not invoke on production, customer, HSTR, CRC, or any unapproved cluster. Local fixture tests and a standalone etcd container test do not establish OpenShift DR readiness.

Only one nominated control-plane source receives a schedule. Backups remain local to that host, so they are not off-host disaster recovery and do not protect PV or application data. Copy sets to an owner-managed encrypted off-host destination outside this repository.

The static-resource archive can contain encryption material. The role creates root-owned `0700` directories and `0600` files. Do not print, commit, or upload archives, manifests, logs, SSH keys, inventories, or kubeconfigs.

HA restore may proceed only after a successful all-host preflight. Initial recovery invokes no `oc`; any documented post-API recovery requires an owner-supplied certificate kubeconfig and supported Machine API mapping. This v0.1 implementation deliberately stops before automatic post-API Machine replacement: it has not been validated in an approved OCP 4.20 lab.
