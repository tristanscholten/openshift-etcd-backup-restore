#!/usr/bin/env python3
"""Validate one offline control-plane host against a selected backup manifest."""
import argparse
import base64
import binascii
import json
import pathlib
import re
import sys

UUID = re.compile(r"^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
SAFE_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")


def read_regular(path: str) -> str:
    candidate = pathlib.Path(path)
    if not candidate.is_absolute() or candidate.is_symlink() or not candidate.is_file():
        raise ValueError("required host identity source is not a regular absolute file")
    return candidate.read_text(encoding="utf-8").strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest-base64", required=True)
    parser.add_argument("--inventory-host", required=True)
    parser.add_argument("--identity-path", required=True)
    parser.add_argument("--machine-config-path", required=True)
    args = parser.parse_args()
    if not SAFE_NAME.fullmatch(args.inventory_host):
        raise ValueError("unsafe inventory hostname")
    manifest = json.loads(base64.b64decode(args.manifest_base64, validate=True))
    records = manifest.get("control_plane")
    if not isinstance(records, list) or len(records) not in (1, 3):
        raise ValueError("invalid control-plane manifest")
    matching = [entry for entry in records if isinstance(entry, dict) and entry.get("node_name") == args.inventory_host]
    if len(matching) != 1:
        raise ValueError("inventory host is not bound to selected backup")
    entry = matching[0]
    expected_uuid = entry.get("system_uuid")
    expected_config = entry.get("machine_config")
    if not isinstance(expected_uuid, str) or not UUID.fullmatch(expected_uuid):
        raise ValueError("invalid captured host identity")
    if not isinstance(expected_config, str) or not SAFE_NAME.fullmatch(expected_config):
        raise ValueError("invalid captured machine configuration")
    actual_uuid = read_regular(args.identity_path)
    if actual_uuid.lower() != expected_uuid.lower():
        raise ValueError("control-plane host identity mismatch")
    current_config = json.loads(read_regular(args.machine_config_path))
    actual_config = current_config.get("metadata", {}).get("name")
    if actual_config != expected_config:
        raise ValueError("control-plane installed release configuration mismatch")
    print(json.dumps({"inventory_host": args.inventory_host, "validated": True}, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, json.JSONDecodeError, binascii.Error) as exc:
        print(f"RESTORE_HOST_VALIDATION_FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
