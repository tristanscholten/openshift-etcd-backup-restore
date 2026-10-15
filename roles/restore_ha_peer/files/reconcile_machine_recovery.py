#!/usr/bin/env python3
"""Read-only post-recovery HA identity reconciliation for the restore playbook."""
import json
import pathlib
import subprocess
import sys


FAILURE = "POST_API_MAP_RECONCILIATION_FAILED"
ROLE = "machine.openshift.io/cluster-api-machine-role"


def fail(message: str) -> None:
    raise SystemExit(f"{FAILURE}: {message}")


def get(oc: str, kubeconfig: str, *args: str) -> dict:
    try:
        result = subprocess.run(
            [oc, f"--kubeconfig={kubeconfig}", "get", *args, "-o", "json"],
            check=True, text=True, capture_output=True, timeout=30,
        )
        value = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        fail(f"read-only API discovery failed: {error}")
    if not isinstance(value, dict):
        fail("malformed API response")
    return value


def text(obj: dict, *keys: str) -> str:
    value = obj
    for key in keys:
        if not isinstance(value, dict):
            return ""
        value = value.get(key)
    return value if isinstance(value, str) else ""


def index(items: list) -> dict:
    answer = {}
    for item in items:
        name = text(item, "metadata", "name")
        if not name or name in answer:
            fail("duplicate or missing object name")
        answer[name] = item
    return answer


def control_machine(machine: dict) -> bool:
    return machine.get("metadata", {}).get("labels", {}).get(ROLE) == "master"


def healthy_operators(operators: list) -> bool:
    required = {"etcd", "kube-apiserver", "machine-api"}
    found = {}
    for operator in operators:
        name = text(operator, "metadata", "name")
        if name in required:
            found[name] = {
                condition.get("type"): condition.get("status")
                for condition in operator.get("status", {}).get("conditions", [])
                if isinstance(condition, dict)
            }
    return set(found) == required and all(
        conditions.get("Available") == "True" and conditions.get("Degraded") == "False"
        for conditions in found.values()
    )


def replacement_path(output: pathlib.Path, host: str) -> pathlib.Path:
    return output / f"{host}.replacement.json"


def expected_records(control: dict, output: pathlib.Path) -> dict:
    records = {entry["inventory_hostname"]: dict(entry) for entry in [control["recovery_host"], *control["non_recovery"]]}
    if len(records) != 3:
        fail("invalid captured control-plane inventory")
    for host, record in records.items():
        checkpoint = replacement_path(output, host)
        if checkpoint.exists():
            try:
                replacement = json.loads(checkpoint.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                fail(f"invalid replacement checkpoint: {error}")
            if not isinstance(replacement, dict):
                fail("invalid replacement checkpoint")
            record.update({
                "machine_name": replacement.get("machine_name"),
                "machine_uid": replacement.get("machine_uid"),
                "node_name": replacement.get("node_name"),
                "node_uid": replacement.get("node_uid"),
                "provider_id": replacement.get("provider_id"),
                "machine_node_ref_name": replacement.get("node_name"),
                "machine_node_ref_uid": replacement.get("node_uid"),
                "machine_provider_id": replacement.get("provider_id"),
            })
    return records


def check_machine(machine: dict, record: dict) -> None:
    required = ("machine_name", "machine_uid", "node_name", "node_uid", "provider_id", "machine_node_ref_name", "machine_node_ref_uid", "machine_provider_id")
    if not all(isinstance(record.get(field), str) and record[field] for field in required):
        fail("captured Machine identity incomplete")
    if not control_machine(machine):
        fail("Machine is not a control-plane Machine")
    actual = (
        text(machine, "metadata", "name"), text(machine, "metadata", "uid"),
        text(machine, "status", "nodeRef", "name"), text(machine, "status", "nodeRef", "uid"),
        text(machine, "spec", "providerID"),
    )
    expected = (
        record["machine_name"], record["machine_uid"], record["machine_node_ref_name"],
        record["machine_node_ref_uid"], record["machine_provider_id"],
    )
    if actual != expected or record["node_name"] != expected[2] or record["node_uid"] != expected[3] or record["provider_id"] != expected[4]:
        fail("Machine UID/NodeRef/providerID mismatch")


def check_cpms(oc: str, kubeconfig: str, namespace: str, cpms: dict, machines: list) -> None:
    if not cpms.get("required"):
        if get(oc, kubeconfig, "controlplanemachineset.machine.openshift.io", "-n", namespace).get("items"):
            fail("CPMS unexpectedly present")
        return
    current = get(oc, kubeconfig, "controlplanemachineset.machine.openshift.io", cpms["name"], "-n", namespace)
    if (current.get("apiVersion") != "machine.openshift.io/v1" or text(current, "metadata", "uid") != cpms["uid"]
            or current.get("spec", {}).get("replicas") != 3 or current.get("spec", {}).get("state") != "Active"):
        fail("CPMS API/UID/state/replicas mismatch")
    for machine in machines:
        references = machine.get("metadata", {}).get("ownerReferences", [])
        if not any(isinstance(reference, dict) and reference.get("uid") == cpms["controller_uid"]
                   and reference.get("kind") == cpms["controller_kind"] and reference.get("controller") is True
                   for reference in references):
            fail("CPMS does not controller-own every expected Machine")


def find_replacement(machines: list, expected: dict, output: pathlib.Path) -> dict:
    candidates = [machine for machine in machines if control_machine(machine)]
    # All other expected Machines have already been checked. Only one unknown master
    # may therefore be the replacement for the just-deleted peer.
    known = {record["machine_name"] for record in expected.values()}
    for path in output.glob("*.replacement.json"):
        try:
            known.add(json.loads(path.read_text(encoding="utf-8"))["machine_name"])
        except (OSError, json.JSONDecodeError, KeyError):
            fail("invalid replacement checkpoint")
    unknown = [machine for machine in candidates if text(machine, "metadata", "name") not in known]
    if len(unknown) != 1:
        fail("replacement Machine identity is absent or ambiguous")
    machine = unknown[0]
    if not all((text(machine, "status", "nodeRef", "name"), text(machine, "status", "nodeRef", "uid"),
                text(machine, "spec", "providerID"), text(machine, "metadata", "uid"))):
        fail("replacement Machine identity incomplete")
    return machine


def write_replacement(output: pathlib.Path, peer: dict, machine: dict) -> None:
    node_name, node_uid, provider_id, machine_uid = (
        text(machine, "status", "nodeRef", "name"), text(machine, "status", "nodeRef", "uid"),
        text(machine, "spec", "providerID"), text(machine, "metadata", "uid"),
    )
    replacement_path(output, peer["inventory_hostname"]).write_text(json.dumps({
        "machine_name": text(machine, "metadata", "name"), "machine_uid": machine_uid,
        "node_name": node_name, "node_uid": node_uid, "provider_id": provider_id,
    }, sort_keys=True), encoding="utf-8")


def main() -> None:
    if len(sys.argv) not in (5, 6, 7):
        raise SystemExit("usage: reconcile_machine_recovery.py OC KUBECONFIG INVENTORY OUTPUT_DIR [INVENTORY_HOSTNAME] [--replacement]")
    oc, kubeconfig, inventory_path, output_path = sys.argv[1:5]
    requested_host = sys.argv[5] if len(sys.argv) >= 6 and sys.argv[5] != "--replacement" else ""
    replacement = sys.argv[-1] == "--replacement"
    inventory = json.loads(pathlib.Path(inventory_path).read_text(encoding="utf-8"))
    control = inventory["control_plane"]
    peers = control["non_recovery"]
    if len(peers) != 2:
        fail("invalid captured peer count")
    selected = [peer for peer in peers if peer.get("inventory_hostname") == requested_host] if requested_host else peers
    if requested_host and len(selected) != 1:
        fail("requested captured peer is absent")
    output = pathlib.Path(output_path)
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    expected = expected_records(control, output)
    machines = get(oc, kubeconfig, "machines", "-n", inventory["machine_api"]["namespace"]).get("items")
    nodes = get(oc, kubeconfig, "nodes").get("items")
    operators = get(oc, kubeconfig, "clusteroperators").get("items")
    if not isinstance(machines, list) or not isinstance(nodes, list) or not isinstance(operators, list):
        fail("list response malformed")
    if not healthy_operators(operators):
        fail("required phase-appropriate post-recovery operators are unhealthy")
    by_machine, by_node = index(machines), index(nodes)
    masters = [machine for machine in machines if control_machine(machine)]
    if len(masters) != 3:
        fail("exactly three control-plane Machines are required")
    if len({text(machine, "status", "nodeRef", "name") for machine in masters}) != 3:
        fail("selected control-plane Machines have duplicate NodeRefs")
    replacement_machine = None
    if replacement:
        target = selected[0]
        if target["machine_name"] in by_machine:
            fail("deleted Machine still exists")
        expected.pop(target["inventory_hostname"], None)
    for record in expected.values():
        node = by_node.get(record["node_name"])
        machine = by_machine.get(record["machine_name"])
        if not node or text(node, "metadata", "uid") != record["node_uid"] or text(node, "spec", "providerID") != record["provider_id"]:
            fail("Node UID/providerID mismatch")
        if not machine:
            fail("captured Machine absent")
        check_machine(machine, record)
    if replacement:
        replacement_machine = find_replacement(masters, expected, output)
    current_machines = [by_machine[record["machine_name"]] for record in expected.values()]
    if replacement_machine:
        current_machines.append(replacement_machine)
    check_cpms(oc, kubeconfig, inventory["machine_api"]["namespace"], inventory["cpms"], current_machines)
    if replacement:
        if replacement_machine is None:
            fail("replacement Machine identity is absent")
        write_replacement(output, selected[0], replacement_machine)
    else:
        for peer in selected:
            (output / f"{peer['inventory_hostname']}.machine.json").write_text(json.dumps(by_machine[peer["machine_name"]], sort_keys=True), encoding="utf-8")
    (output / "POST_API_MAP_RECONCILED").write_text("\n", encoding="utf-8")


if __name__ == "__main__":
    main()
