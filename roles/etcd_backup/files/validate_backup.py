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


def fail(message: str) -> NoReturn:
    raise SystemExit(message)


def regular(path):
    return path.is_file() and not path.is_symlink() and stat.S_ISREG(path.stat().st_mode)


def valid_set(root, set_id):
    if not SET_ID.fullmatch(set_id):
        fail("unsafe set id")
    root = pathlib.Path(root).resolve(strict=True)
    if root.is_symlink() or not root.is_dir():
        fail("unsafe root")
    candidate = root / set_id
    if candidate.parent != root or candidate.is_symlink() or not candidate.is_dir():
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
    if not required <= data.keys() or data["set_id"] != set_id or data["schema"] != 1:
        fail("invalid metadata")
    if not re.fullmatch(r"4\.20\.\d+", str(data["ocp_version"])):
        fail("unsupported backup version")
    timestamp = set_id.removeprefix("set-")
    if data["timestamp"] != timestamp or data["snapshot"] != f"snapshot_{timestamp}.db" or data["static_resources"] != f"static_kuberesources_{timestamp}.tar.gz":
        fail("mismatched backup pair")
    entries = {}
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
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if entries.get(name) != digest:
            fail("hash mismatch")
    return data


if __name__ == "__main__":
    if len(sys.argv) != 3:
        fail("usage: validate_backup.py ROOT SET_ID")
    print(json.dumps(valid_set(sys.argv[1], sys.argv[2]), sort_keys=True))
