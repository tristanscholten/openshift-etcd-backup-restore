#!/usr/bin/env python3
"""Read-only post-recovery HA identity reconciliation for the restore playbook."""
import json
import pathlib
import subprocess
import sys


def fail(message: str) -> None:
    raise SystemExit(message)


def get(oc: str, kubeconfig: str, *args: str) -> dict:
    try:
        result = subprocess.run([oc, f"--kubeconfig={kubeconfig}", "get", *args, "-o", "json"], check=True, text=True, capture_output=True, timeout=30)
        value = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        fail(f"POST_API_MAP_RECONCILIATION_FAILED: read-only API discovery failed: {error}")
    if not isinstance(value, dict):
        fail("POST_API_MAP_RECONCILIATION_FAILED: malformed API response")
    return value


def index(items: list, field: str = "name") -> dict:
    answer = {}
    for item in items:
        meta = item.get("metadata", {})
        value = meta.get(field)
        if not isinstance(value, str) or not value or value in answer:
            fail("POST_API_MAP_RECONCILIATION_FAILED: duplicate or missing identity")
        answer[value] = item
    return answer


def text(obj: dict, *keys: str) -> str:
    value = obj
    for key in keys:
        if not isinstance(value, dict):
            return ""
        value = value.get(key)
    return value if isinstance(value, str) else ""


def main() -> None:
    if len(sys.argv) not in (5, 6):
        fail("usage: reconcile_machine_recovery.py OC KUBECONFIG INVENTORY OUTPUT_DIR [INVENTORY_HOSTNAME]")
    oc, kubeconfig, inventory_path, output_path = sys.argv[1:5]
    requested_host = sys.argv[5] if len(sys.argv) == 6 else ""
    inventory = json.loads(pathlib.Path(inventory_path).read_text(encoding="utf-8"))
    control = inventory["control_plane"]
    peers = control["non_recovery"]
    if len(peers) != 2:
        fail("POST_API_MAP_RECONCILIATION_FAILED: invalid captured peer count")
    if requested_host:
        peers = [peer for peer in peers if peer.get("inventory_hostname") == requested_host]
        if len(peers) != 1:
            fail("POST_API_MAP_RECONCILIATION_FAILED: requested captured peer is absent")
    namespace = inventory["machine_api"]["namespace"]
    machines = get(oc, kubeconfig, "machines", "-n", namespace).get("items")
    nodes = get(oc, kubeconfig, "nodes").get("items")
    if not isinstance(machines, list) or not isinstance(nodes, list):
        fail("POST_API_MAP_RECONCILIATION_FAILED: list response malformed")
    by_machine, by_node = index(machines), index(nodes)
    expected = {peer["machine_name"] for peer in peers}
    if len(expected) != len(peers):
        fail("POST_API_MAP_RECONCILIATION_FAILED: duplicate captured Machine")
    output = pathlib.Path(output_path)
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    for peer in peers:
        machine = by_machine.get(peer["machine_name"])
        node = by_node.get(peer["node_name"])
        if not machine or not node:
            fail("POST_API_MAP_RECONCILIATION_FAILED: captured Machine or Node absent")
        if text(machine, "metadata", "uid") != peer["machine_uid"] or text(machine, "status", "nodeRef", "name") != peer["machine_node_ref_name"] or text(machine, "status", "nodeRef", "uid") != peer["machine_node_ref_uid"] or text(machine, "spec", "providerID") != peer["machine_provider_id"]:
            fail("POST_API_MAP_RECONCILIATION_FAILED: Machine UID/NodeRef/providerID mismatch")
        if text(node, "metadata", "uid") != peer["node_uid"] or text(node, "spec", "providerID") != peer["provider_id"]:
            fail("POST_API_MAP_RECONCILIATION_FAILED: Node UID/providerID mismatch")
        labels = machine.get("metadata", {}).get("labels", {})
        if labels.get("machine.openshift.io/cluster-api-machine-role") != "master":
            fail("POST_API_MAP_RECONCILIATION_FAILED: Machine is not a control-plane Machine")
        # oc delete -f preserves metadata.uid as the Kubernetes DeleteOptions UID
        # precondition, blocking a name-reuse race.
        (output / f"{peer['inventory_hostname']}.machine.json").write_text(json.dumps(machine, sort_keys=True), encoding="utf-8")
    cpms = inventory["cpms"]
    if cpms.get("required"):
        current = get(oc, kubeconfig, "controlplanemachineset.machine.openshift.io", cpms["name"], "-n", namespace)
        if text(current, "metadata", "uid") != cpms["uid"] or current.get("spec", {}).get("replicas") != 3 or current.get("spec", {}).get("state") != "Active":
            fail("POST_API_MAP_RECONCILIATION_FAILED: CPMS UID/state/replicas mismatch")
        for machine in by_machine.values():
            if machine.get("metadata", {}).get("name") not in expected:
                continue
            references = machine.get("metadata", {}).get("ownerReferences", [])
            if not any(isinstance(reference, dict) and reference.get("uid") == cpms["controller_uid"] and reference.get("kind") == cpms["controller_kind"] and reference.get("controller") is True for reference in references):
                fail("POST_API_MAP_RECONCILIATION_FAILED: CPMS does not controller-own captured Machine")
    else:
        result = subprocess.run([oc, f"--kubeconfig={kubeconfig}", "get", "controlplanemachineset.machine.openshift.io", "-n", namespace, "-o", "json"], text=True, capture_output=True, timeout=30)
        if result.returncode == 0 and json.loads(result.stdout).get("items"):
            fail("POST_API_MAP_RECONCILIATION_FAILED: CPMS unexpectedly present")
    (output / "POST_API_MAP_RECONCILED").write_text("\n", encoding="utf-8")


if __name__ == "__main__":
    main()
