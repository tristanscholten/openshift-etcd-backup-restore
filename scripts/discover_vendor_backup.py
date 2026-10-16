#!/usr/bin/env python3
"""Read-only validation of CronJob vendor backup manifests."""
import hashlib
import json
import pathlib
import re
import stat
import sys

STAMP = re.compile(r"^[0-9]{8}T[0-9]{6}Z$")
VERSION = re.compile(r"^4\.20\.[0-9]+$")
UUID = re.compile(r"^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
OS_IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()/-]{0,255}$")
PREFIX = ".ocp-etcd-cronjob-managed-"


def regular(path):
    return path.is_file() and not path.is_symlink() and stat.S_ISREG(path.stat().st_mode)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate(root, timestamp):
    if not STAMP.fullmatch(timestamp):
        raise ValueError("unsafe backup timestamp")
    root = pathlib.Path(root).resolve(strict=True)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("unsafe backup root")
    manifest = root / f"{PREFIX}{timestamp}.json"
    snapshot = root / f"snapshot_{timestamp}.db"
    resources = root / f"static_kuberesources_{timestamp}.tar.gz"
    if not all(regular(path) for path in (manifest, snapshot, resources)):
        raise ValueError("incomplete vendor backup pair")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    required = {"schema", "timestamp", "snapshot", "static_resources", "cluster_id", "ocp_version", "snapshot_sha256", "static_resources_sha256", "topology", "cpms_enabled", "control_plane"}
    if not required <= data.keys() or data["schema"] != 1 or data["timestamp"] != timestamp:
        raise ValueError("invalid backup manifest")
    if data["snapshot"] != snapshot.name or data["static_resources"] != resources.name:
        raise ValueError("mismatched backup pair")
    if not VERSION.fullmatch(data["ocp_version"]) or not UUID.fullmatch(data["cluster_id"]):
        raise ValueError("invalid backup release or cluster identity")
    expected_count = 1 if data["topology"] == "sno" else 3 if data["topology"] == "ha" else 0
    records = data["control_plane"]
    if data["cpms_enabled"] is not False or not isinstance(records, list) or len(records) != expected_count:
        raise ValueError("unsupported captured topology or CPMS state")
    names, identities = set(), set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("invalid control-plane record")
        name, identity, config, os_image = record.get("node_name"), record.get("system_uuid"), record.get("machine_config"), record.get("os_image")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
            raise ValueError("invalid captured node name")
        if not isinstance(identity, str) or not UUID.fullmatch(identity) or not isinstance(config, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", config) or not isinstance(os_image, str) or not OS_IMAGE.fullmatch(os_image):
            raise ValueError("invalid captured host identity, configuration, or RHCOS build")
        names.add(name)
        identities.add(identity.lower())
    if len(names) != expected_count or len(identities) != expected_count:
        raise ValueError("duplicate captured control-plane identity")
    if data["snapshot_sha256"] != sha256(snapshot) or data["static_resources_sha256"] != sha256(resources):
        raise ValueError("backup manifest hash mismatch")
    return data


def main():
    if len(sys.argv) not in (2, 3):
        raise SystemExit("usage: discover_vendor_backup.py ROOT [TIMESTAMP]")
    root = pathlib.Path(sys.argv[1])
    if len(sys.argv) == 3:
        print(json.dumps(validate(root, sys.argv[2]), sort_keys=True))
        return
    candidates = []
    for manifest in sorted(root.glob(f"{PREFIX}*.json"), reverse=True):
        timestamp = manifest.name.removeprefix(PREFIX).removesuffix(".json")
        try:
            candidates.append(validate(root, timestamp))
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    print(json.dumps(candidates, sort_keys=True))


if __name__ == "__main__":
    main()
