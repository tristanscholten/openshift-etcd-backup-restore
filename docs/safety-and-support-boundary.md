# Safety and support boundary

Supported baseline is OCP `4.20.z` only, where every control-plane host and the selected set match exactly. The tool rejects unknown minors, mixed versions, invalid hashes, unsafe paths, incomplete pairs, unsupported control-plane counts, incomplete inventory, and a missing explicit confirmation.

Do not invoke on production, customer, HSTR, CRC, or any unapproved cluster. Local fixture tests and a standalone etcd container test do not establish OpenShift DR readiness.

Only one nominated control-plane source receives a schedule. Backups remain local to that host, so they are not off-host disaster recovery and do not protect PV or application data. Copy sets to an owner-managed encrypted off-host destination outside this repository.

The static-resource archive can contain encryption material. The role creates root-owned `0700` directories and `0600` files. Do not print, commit, or upload archives, manifests, logs, SSH keys, inventories, or kubeconfigs.

HA restore is supported only when all preflight gates pass: exactly three explicit control-plane hosts; one nominated source; one recovery host; owner-supplied certificate kubeconfig; and a two-entry mapping from the non-recovery inventory hostnames to Machine API Machine names. Before pausing timers, the playbook rejects UPI/bare metal, unknown/mixed versions, a missing mapping, an incomplete source set, or a stale destructive checkpoint. It copies and re-hashes the selected source set on the recovery host, fences every peer with the installed vendor script and bounded `crictl` absence check, invokes the vendor restore once, and only after API recovery performs the documented quorum guard, mapped Machine recreation, stability wait, and guard clear. No cluster was contacted by this repository's tests.
