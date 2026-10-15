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
PREFIX = ".ocp-etcd-cronjob-managed-"


def regular(path):
    return path.is_file() and not path.is_symlink() and stat.S_ISREG(path.stat().st_mode)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    required = {"schema", "timestamp", "snapshot", "static_resources", "cluster_id", "ocp_version", "snapshot_sha256", "static_resources_sha256"}
    if not required <= data.keys() or data["schema"] != 1 or data["timestamp"] != timestamp:
        raise ValueError("invalid backup manifest")
    if data["snapshot"] != snapshot.name or data["static_resources"] != resources.name:
        raise ValueError("mismatched backup pair")
    if not VERSION.fullmatch(data["ocp_version"]) or not UUID.fullmatch(data["cluster_id"]):
        raise ValueError("invalid backup release or cluster identity")
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
