#!/usr/bin/env python3
"""Read-only validation of one managed backup set; prints sanitized metadata."""
import hashlib
import json
import pathlib
import re
import stat
import sys
from typing import NoReturn

SET_ID = re.compile(r"^set-[0-9]{8}T[0-9]{6}Z$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
MACHINE_API = "machine.openshift.io/v1beta1"
CPMS_API = "controlplanemachineset.machine.openshift.io/v1"


def fail(message: str) -> NoReturn:
    raise SystemExit(message)


def regular(path: pathlib.Path) -> bool:
    return path.is_file() and not path.is_symlink() and stat.S_ISREG(path.stat().st_mode)


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_pair_payload(data: dict) -> bytes:
    """Return the non-circular payload that binds a recovery inventory to a pair.

    This intentionally excludes metadata.json and the inventory itself: both include
    the resulting digest, so including either would create a circular checksum.
    """
    payload = {
        "cluster_id": data["cluster_id"],
        "ocp_version": data["ocp_version"],
        "set_id": data["set_id"],
        "snapshot": data["snapshot"],
        "snapshot_sha256": data["snapshot_sha256"],
        "static_resources": data["static_resources"],
        "static_resources_sha256": data["static_resources_sha256"],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def require_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        fail(f"invalid inventory {label}")
    return value


def validate_machine_inventory(candidate: pathlib.Path, data: dict) -> None:
    inventory_name = data.get("machine_recovery_inventory")
    inventory_digest = data.get("machine_recovery_inventory_sha256")
    if not isinstance(inventory_name, str) or inventory_name != "machine-recovery-inventory-v1.json":
        fail("invalid HA inventory reference")
    if not isinstance(inventory_digest, str) or not SHA256.fullmatch(inventory_digest):
        fail("invalid HA inventory digest")
    inventory_path = candidate / inventory_name
    if inventory_path.parent != candidate or not regular(inventory_path):
        fail("invalid HA inventory path")
    if stat.S_IMODE(inventory_path.stat().st_mode) != 0o600:
        fail("unsafe HA inventory mode")
    raw = inventory_path.read_bytes()
    if digest_bytes(raw) != inventory_digest:
        fail("HA inventory digest mismatch")
    if data.get("_checksums", {}).get(inventory_name) != inventory_digest:
        fail("HA inventory missing from checksum manifest")
    try:
        inventory = json.loads(raw)
    except json.JSONDecodeError as error:
        fail(f"invalid HA inventory JSON: {error}")
    if not isinstance(inventory, dict) or inventory.get("schema_version") != 1:
        fail("unsupported HA inventory schema")
    capture = inventory.get("capture")
    machine_api = inventory.get("machine_api")
    control_plane = inventory.get("control_plane")
    cpms = inventory.get("cpms")
    if not isinstance(capture, dict):
        fail("incomplete HA inventory capture")
    if not isinstance(machine_api, dict):
        fail("incomplete HA Machine API inventory")
    if not isinstance(control_plane, dict):
        fail("incomplete HA control-plane inventory")
    if not isinstance(cpms, dict):
        fail("incomplete HA CPMS inventory")
    if capture.get("backup_set_id") != data["set_id"]:
        fail("HA inventory set mismatch")
    if capture.get("pair_payload_sha256") != data["pair_payload_sha256"]:
        fail("HA inventory pair binding mismatch")
    if capture.get("cluster_id") != data["cluster_id"] or capture.get("ocp_version") != data["ocp_version"]:
        fail("HA inventory cluster/version mismatch")
    if machine_api.get("api_version") != MACHINE_API or machine_api.get("namespace") != "openshift-machine-api":
        fail("unsupported Machine API inventory")
    if control_plane.get("replicas") != 3:
        fail("unsupported HA inventory replica count")
    recovery = control_plane.get("recovery_host")
    peers = control_plane.get("non_recovery")
    if not isinstance(recovery, dict) or not isinstance(peers, list) or len(peers) != 2:
        fail("incomplete HA host inventory")
    identities: set[str] = set()
    machines: set[str] = set()
    for entry in [recovery, *peers]:
        if not isinstance(entry, dict):
            fail("invalid HA host inventory")
        for field in ("inventory_hostname", "ssh_host_identity", "node_name", "node_uid", "provider_id"):
            require_string(entry.get(field), field)
        identity = require_string(entry["ssh_host_identity"], "ssh_host_identity")
        if identity in identities:
            fail("duplicate HA SSH identity")
        identities.add(identity)
    for peer in peers:
        for field in ("machine_name", "machine_uid", "machine_node_ref_name", "machine_node_ref_uid", "machine_provider_id"):
            require_string(peer.get(field), field)
        machine = require_string(peer["machine_name"], "machine_name")
        if machine in machines:
            fail("duplicate HA Machine identity")
        machines.add(machine)
    required_cpms = cpms.get("required")
    if not isinstance(required_cpms, bool):
        fail("invalid CPMS requirement")
    if required_cpms:
        if cpms.get("api_version") != CPMS_API or cpms.get("replicas") != 3 or cpms.get("state") != "Active":
            fail("unsupported CPMS inventory")
        for field in ("name", "uid", "controller_kind", "controller_uid"):
            require_string(cpms.get(field), field)
    elif cpms.get("observed_absent") is not True:
        fail("ambiguous CPMS absence")


def valid_set(root: str, set_id: str) -> dict:
    if not SET_ID.fullmatch(set_id):
        fail("unsafe set id")
    root_path = pathlib.Path(root).resolve(strict=True)
    if root_path.is_symlink() or not root_path.is_dir():
        fail("unsafe root")
    candidate = root_path / set_id
    if candidate.parent != root_path or candidate.is_symlink() or not candidate.is_dir():
        fail("invalid set directory")
    complete = candidate / "COMPLETE"
    metadata = candidate / "metadata.json"
    sums = candidate / "SHA256SUMS"
    if not all(regular(path) for path in (complete, metadata, sums)):
        fail("incomplete set")
    try:
        data = json.loads(metadata.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"invalid metadata: {error}")
    required = {"schema", "set_id", "timestamp", "ocp_version", "cluster_id", "snapshot", "static_resources"}
    if not required <= data.keys() or data["set_id"] != set_id or data["schema"] not in (1, 2):
        fail("invalid metadata")
    if not re.fullmatch(r"4\.20\.\d+", str(data["ocp_version"])):
        fail("unsupported backup version")
    timestamp = set_id.removeprefix("set-")
    if data["timestamp"] != timestamp or data["snapshot"] != f"snapshot_{timestamp}.db" or data["static_resources"] != f"static_kuberesources_{timestamp}.tar.gz":
        fail("mismatched backup pair")
    entries: dict[str, str] = {}
    try:
        for line in sums.read_text(encoding="utf-8").splitlines():
            digest, name = line.split(maxsplit=1)
            if not SHA256.fullmatch(digest) or name in entries:
                fail("invalid checksum manifest")
            entries[name] = digest
    except ValueError:
        fail("invalid checksum manifest")
    for name in (data["snapshot"], data["static_resources"]):
        path = candidate / name
        if path.parent != candidate or not regular(path):
            fail("invalid backup member")
        digest = digest_bytes(path.read_bytes())
        if entries.get(name) != digest:
            fail("hash mismatch")
    if data["schema"] == 2:
        data["snapshot_sha256"] = entries[data["snapshot"]]
        data["static_resources_sha256"] = entries[data["static_resources"]]
        expected = digest_bytes(canonical_pair_payload(data))
        if data.get("pair_payload_sha256") != expected:
            fail("pair payload digest mismatch")
        data["_checksums"] = entries
        if data.get("restore_topology") not in ("sno", "ha"):
            fail("invalid backup topology")
        if data.get("restore_topology") == "ha":
            validate_machine_inventory(candidate, data)
        data.pop("_checksums", None)
    return data


if __name__ == "__main__":
    if len(sys.argv) != 3:
        fail("usage: validate_backup.py ROOT SET_ID")
    print(json.dumps(valid_set(sys.argv[1], sys.argv[2]), sort_keys=True))
