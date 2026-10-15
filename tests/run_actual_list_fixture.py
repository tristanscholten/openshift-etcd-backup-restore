#!/usr/bin/env python3
"""SIMULATED actual list-backups.yml execution; no OpenShift cluster is contacted."""
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLAYBOOK = ROOT / "playbooks" / "list-backups.yml"
VALIDATOR = ROOT / "scripts" / "validate_backup.py"
GOOD_ID = "set-20261015T000000Z"
BAD_ID = "set-20261015T060000Z"


def make_set(root: pathlib.Path, set_id: str, corrupt: bool = False) -> None:
    directory = root / set_id
    directory.mkdir(parents=True)
    timestamp = set_id.removeprefix("set-")
    snapshot = f"snapshot_{timestamp}.db"
    resources = f"static_kuberesources_{timestamp}.tar.gz"
    (directory / snapshot).write_text("snapshot\n")
    (directory / resources).write_text("static resources\n")
    (directory / "SHA256SUMS").write_text("".join(
        f"{hashlib.sha256((directory / name).read_bytes()).hexdigest()}  {name}\n"
        for name in (snapshot, resources)
    ))
    (directory / "metadata.json").write_text(json.dumps({
        "schema": 1, "set_id": set_id, "timestamp": timestamp,
        "ocp_version": "4.20.3", "cluster_id": "fixture",
        "snapshot": snapshot, "static_resources": resources,
    }))
    (directory / "COMPLETE").write_text("")
    if corrupt:
        (directory / snapshot).write_text("corrupt\n")


def main() -> None:
    fixture = pathlib.Path(tempfile.mkdtemp(prefix="actual-list-", dir=os.environ.get("TMPDIR")))
    try:
        root = fixture / "backups"
        make_set(root, GOOD_ID)
        make_set(root, BAD_ID, corrupt=True)
        wrapper = fixture / "validator"
        wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {VALIDATOR} \"$@\"\n")
        wrapper.chmod(0o700)
        inventory = fixture / "inventory.yml"
        inventory.write_text("all:\n  children:\n    control_plane:\n      hosts:\n        source: {ansible_connection: local}\n")
        result = subprocess.run([
            "ansible-playbook", "-i", str(inventory), str(PLAYBOOK),
            "-e", "ansible_become=false", "-e", "backup_source_hostname=source",
            "-e", f"backup_root={root}", "-e", f"backup_validator_path={wrapper}",
        ], text=True, capture_output=True)
        output = result.stdout + result.stderr
        if result.returncode:
            raise AssertionError(output)
        if GOOD_ID not in output or BAD_ID in output:
            raise AssertionError(output)
        if "snapshot\n" in output or "static resources\n" in output:
            raise AssertionError("list output exposed backup content")
        print("SIMULATED_ACTUAL_LIST_PLAYBOOK_OK")
    finally:
        shutil.rmtree(fixture, ignore_errors=True)


if __name__ == "__main__":
    main()
