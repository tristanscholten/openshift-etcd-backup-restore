# Safety and support boundary

Supported baseline is OCP `4.20.z` only, where every control-plane host and the selected set match exactly. The tool rejects unknown minors, mixed versions, invalid hashes, unsafe paths, incomplete pairs, unsupported control-plane counts, incomplete inventory, and a missing explicit confirmation.

Do not invoke on production, customer, HSTR, CRC, or any unapproved cluster. Local fixture tests and a standalone etcd container test do not establish OpenShift DR readiness.

Only one nominated control-plane source receives a schedule. Backups remain local to that host, so they are not off-host disaster recovery and do not protect PV or application data. Copy sets to an owner-managed encrypted off-host destination outside this repository.

The static-resource archive can contain encryption material. The role creates root-owned `0700` directories and `0600` files. Do not print, commit, or upload archives, manifests, logs, SSH keys, inventories, or kubeconfigs.

Supported HA restore is only the CronJob-pair path: exactly three explicit non-CPMS control-plane hosts, a selected manifest whose healthy-time records bind the same three inventory names, DMI system UUIDs, current MachineConfigs, and RHCOS OS-image builds, a nominated source/recovery host, and an owner-supplied certificate kubeconfig. Before confirmation or fencing, it rejects a wrong topology, CPMS, host identity, MachineConfig, host-local `/etc/os-release` RHCOS build, cluster/release, incomplete source pair, or stale checkpoint. RHCOS build identity is separate from the exact ClusterVersion OCP `4.20.z` binding; neither a rendered MachineConfig name nor an `oc` client RPM is treated as that release. It copies/re-hashes the pair on the recovery host, fences every peer, invokes vendor restore once, then after API recovery applies the documented quorum guard, waits for stability, and clears the guard only after success. It never deletes Machines. No cluster was contacted by this repository's tests.
