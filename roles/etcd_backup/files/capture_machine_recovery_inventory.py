#!/usr/bin/env python3
"""Capture allow-listed OCP HA Machine identity data during a healthy backup.

This helper is intentionally invoked only by the backup runner.  It reads the
configured root-only kubeconfig through oc and writes no credentials or full
API objects.  It is not used by an offline restore.
"""
import json
import os
import pathlib
import subprocess
import sys
from datetime import datetime, timezone

MACHINE_API = "machine.openshift.io/v1beta1"
CPMS_API = "controlplanemachineset.machine.openshift.io/v1"
NAMESPACE = "openshift-machine-api"
ROLE = "machine.openshift.io/cluster-api-machine-role"


def die(message: str) -> None:
    raise SystemExit(message)


def nonempty(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        die(f"capture missing {label}")
    return value


def oc_json(*args: str) -> dict:
    oc = os.environ.get("OCP_ETCD_BACKUP_OC", "/usr/bin/oc")
    kubeconfig = os.environ.get("OCP_ETCD_BACKUP_CAPTURE_KUBECONFIG", "")
    if not pathlib.Path(oc).is_absolute() or not pathlib.Path(oc).is_file() or not os.access(oc, os.X_OK):
        die("capture requires executable absolute OCP_ETCD_BACKUP_OC")
    path = pathlib.Path(kubeconfig)
    if not path.is_absolute() or path.is_symlink() or not path.is_file() or not os.access(path, os.R_OK):
        die("capture requires readable regular root-only kubeconfig")
    try:
        result = subprocess.run([oc, "--kubeconfig", kubeconfig, *args, "-o", "json"], check=True,
                                capture_output=True, text=True, timeout=30)
        value = json.loads(result.stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError) as error:
        die(f"capture API read failed: {error}")
    if not isinstance(value, dict):
        die("capture API returned non-object")
    return value


def obj_field(obj: dict, *path: str) -> str:
    value = obj
    for key in path:
        if not isinstance(value, dict):
            die("capture malformed API object")
        value = value.get(key)
    return nonempty(value, ".".join(path))


def main() -> None:
    if len(sys.argv) != 4:
        die("usage: capture_machine_recovery_inventory.py PAIR_JSON SET_ID OUTPUT")
    pair = json.loads(sys.argv[1])
    set_id, output = sys.argv[2:]
    config_path = pathlib.Path(os.environ.get("OCP_ETCD_BACKUP_HA_CAPTURE_CONFIG", ""))
    if not config_path.is_absolute() or config_path.is_symlink() or not config_path.is_file():
        die("capture requires root-only HA capture configuration")
    if config_path.stat().st_mode & 0o077:
        die("unsafe HA capture configuration mode")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    hosts = config.get("control_plane_hosts")
    recovery_name = config.get("recovery_host")
    identity_command = config.get("ssh_host_identity_command")
    if (not isinstance(hosts, list) or len(hosts) != 3 or not isinstance(recovery_name, str)
            or not isinstance(identity_command, str) or not identity_command.startswith("/")
            or any(character.isspace() for character in identity_command)):
        die("capture requires exactly three configured control-plane hosts")
    seen = set()
    host_by_node = {}
    for item in hosts:
        if not isinstance(item, dict): die("invalid configured host")
        name = nonempty(item.get("inventory_hostname"), "inventory_hostname")
        identity = nonempty(item.get("ssh_host_identity"), "ssh_host_identity")
        if name in seen or identity in host_by_node: die("duplicate configured HA host identity")
        seen.add(name)
        host_by_node[nonempty(item.get("node_name"), "node_name")] = {"inventory_hostname": name, "ssh_host_identity": identity}
    if recovery_name not in seen: die("configured recovery host is absent")

    infra = oc_json("get", "infrastructure", "cluster")
    infra_status = infra.get("status", {})
    cluster_id = nonempty(infra_status.get("infrastructureName"), "infrastructureName")
    # InfrastructureName is platform-scoped proof used with actual Machine providerIDs,
    # never a free-form owner platform label.
    machines = oc_json("get", "machines", "-n", NAMESPACE).get("items", [])
    nodes = oc_json("get", "nodes").get("items", [])
    if not isinstance(machines, list) or not isinstance(nodes, list): die("capture list response malformed")
    node_records = {}
    for node in nodes:
        labels = node.get("metadata", {}).get("labels", {})
        if labels.get("node-role.kubernetes.io/control-plane") != "": continue
        node_records[obj_field(node, "metadata", "name")] = {
            "node_uid": obj_field(node, "metadata", "uid"),
            "provider_id": obj_field(node, "spec", "providerID"),
        }
    if set(node_records) != set(host_by_node): die("configured hosts do not exactly match control-plane Nodes")
    selected = []
    for machine in machines:
        labels = machine.get("metadata", {}).get("labels", {})
        if labels.get(ROLE) != "master": continue
        ref = machine.get("status", {}).get("nodeRef", {})
        provider_id = machine.get("spec", {}).get("providerID")
        name = ref.get("name")
        if name not in host_by_node or provider_id != node_records[name]["provider_id"] or ref.get("uid") != node_records[name]["node_uid"]:
            die("Machine ownership is not proven by NodeRef and providerID")
        selected.append((machine, name))
    if len(selected) != 3: die("capture requires exactly three mapped control-plane Machines")

    cpms_result = subprocess.run([os.environ.get("OCP_ETCD_BACKUP_OC", "/usr/bin/oc"), "--kubeconfig", os.environ["OCP_ETCD_BACKUP_CAPTURE_KUBECONFIG"], "get", "controlplanemachineset.machine.openshift.io", "cluster", "-n", NAMESPACE, "-o", "json"], capture_output=True, text=True, timeout=30)
    if cpms_result.returncode == 0:
        cpms_obj = json.loads(cpms_result.stdout)
        if obj_field(cpms_obj, "apiVersion") != "machine.openshift.io/v1" or cpms_obj.get("spec", {}).get("state") != "Active" or cpms_obj.get("spec", {}).get("replicas") != 3:
            die("unsupported CPMS state")
        cpms_uid = obj_field(cpms_obj, "metadata", "uid")
        for machine, _ in selected:
            references = machine.get("metadata", {}).get("ownerReferences", [])
            if not any(isinstance(reference, dict) and reference.get("uid") == cpms_uid and reference.get("kind") == "ControlPlaneMachineSet" and reference.get("controller") is True for reference in references):
                die("CPMS does not controller-own every captured control-plane Machine")
        cpms = {"required": True, "api_version": CPMS_API, "name": obj_field(cpms_obj, "metadata", "name"), "uid": cpms_uid, "generation": cpms_obj.get("metadata", {}).get("generation"), "replicas": 3, "state": "Active", "controller_kind": "ControlPlaneMachineSet", "controller_uid": cpms_uid}
        mode = "cpms-active"
    elif cpms_result.returncode != 0 and "NotFound" in cpms_result.stderr:
        # Absence is an explicit supported mode; authorization, transport, and
        # discovery failures must not be misclassified as absent CPMS.
        cpms = {"required": False, "observed_absent": True}
        mode = "machine-api-no-cpms"
    else:
        die("CPMS discovery failed")
    records = {}
    for machine, node_name in selected:
        host = host_by_node[node_name]
        record = {**host, "node_name": node_name, **node_records[node_name]}
        if host["inventory_hostname"] != recovery_name:
            record.update({"machine_name": obj_field(machine, "metadata", "name"), "machine_uid": obj_field(machine, "metadata", "uid"), "machine_node_ref_name": node_name, "machine_node_ref_uid": node_records[node_name]["node_uid"], "machine_provider_id": node_records[node_name]["provider_id"]})
        records[host["inventory_hostname"]] = record
    inventory = {"schema_version": 1, "capture": {"captured_at_rfc3339": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"), "ocp_version": nonempty(pair.get("ocp_version"), "ocp_version"), "cluster_id": nonempty(pair.get("cluster_id"), "cluster_id"), "infrastructure_name": cluster_id, "backup_set_id": set_id, "pair_payload_sha256": nonempty(pair.get("pair_payload_sha256"), "pair_payload_sha256"), "management_mode": mode, "ssh_host_identity_command": identity_command}, "control_plane": {"replicas": 3, "recovery_host": records[recovery_name], "non_recovery": [records[name] for name in sorted(records) if name != recovery_name]}, "machine_api": {"namespace": NAMESPACE, "api_version": MACHINE_API, "control_plane_selector": ROLE + "=master"}, "cpms": cpms}
    pathlib.Path(output).write_text(json.dumps(inventory, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
