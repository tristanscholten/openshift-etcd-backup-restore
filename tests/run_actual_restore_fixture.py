#!/usr/bin/env python3
"""SIMULATED local-host execution of the actual guarded restore playbook.

This fixture uses only local Ansible connections and disposable wrapper commands.
It is orchestration evidence, not an OpenShift DR test.
"""
import argparse
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLAYBOOK = ROOT / "playbooks" / "restore.yml"
VALIDATOR = ROOT / "scripts" / "validate_backup.py"
SET_ID = "set-20261015T000000Z"


def script(path: pathlib.Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o700)


def build_fixture(scenario: str) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path, list[str]]:
    fixture = pathlib.Path(tempfile.mkdtemp(prefix="actual-restore-", dir=os.environ.get("TMPDIR")))
    home, bin_dir, trace = fixture / "backup-home", fixture / "bin", fixture / "trace"
    backup = home / "etcd-backups" / SET_ID
    backup.mkdir(parents=True)
    bin_dir.mkdir()
    trace.write_text("")
    selected_version = "4.20.4" if scenario == "selected-mismatch" else "4.20.3"
    installed_version = "4.21.0" if scenario == "unknown-minor" else "4.20.3"
    snapshot, static = "snapshot_20261015T000000Z.db", "static_kuberesources_20261015T000000Z.tar.gz"
    (backup / snapshot).write_text("snapshot-marker\n")
    (backup / static).write_text("static-marker\n")
    (backup / "SHA256SUMS").write_text("".join(
        f"{hashlib.sha256((backup / name).read_bytes()).hexdigest()}  {name}\n" for name in (snapshot, static)
    ))
    (backup / "metadata.json").write_text(json.dumps({
        "schema": 1, "set_id": SET_ID, "timestamp": "20261015T000000Z",
        "ocp_version": selected_version, "cluster_id": "fixture",
        "snapshot": snapshot, "static_resources": static,
    }))
    (backup / "COMPLETE").write_text("")
    validator = bin_dir / "validator"
    script(validator, f'#!/bin/sh\nprintf "validate:$2\n" >> "{trace}"\nexec "{sys.executable}" "{VALIDATOR}" "$@"\n')
    crictl_body = '#!/bin/sh\nexit 0\n'
    if scenario == "container":
        crictl_body = f'#!/bin/sh\nprintf "etcd-container\n"\nprintf "container:%s\n" "$*" >> "{trace}"\nexit 0\n'
    disable_body = f'#!/bin/sh\nprintf "fence\n" >> "{trace}"\n'
    if scenario == "fence-fail":
        disable_body += "exit 23\n"
    script(bin_dir / "version", f'#!/bin/sh\nprintf "{installed_version}\\n"\n')
    script(bin_dir / "version-alt", '#!/bin/sh\nprintf "4.20.4\\n"\n')
    script(bin_dir / "crictl", crictl_body)
    script(bin_dir / "restore", f'#!/bin/sh\nprintf "vendor-restore\n" >> "{trace}"\n')
    script(bin_dir / "disable", disable_body)
    script(bin_dir / "oc", f'#!/bin/sh\nprintf "oc:%s\n" "$*" >> "{trace}"\n')
    script(bin_dir / "action", f'#!/bin/sh\nprintf "%s:%s\n" "$1" "$2" >> "{trace}"\n')
    certs, manifest, kubeconfig = fixture / "certs", fixture / "manifest", fixture / "kubeconfig"
    certs.mkdir(); os.utime(certs, (1, 1))
    manifest.write_text("manifest\n"); manifest.chmod(0o700)
    kubeconfig.write_text("apiVersion: v1\nclusters: []\n")
    hosts = ["cp0"] if scenario == "sno" else ["cp0", "cp1", "cp2"]
    inventory = fixture / "inventory.yml"
    host_lines = []
    for host in hosts:
        command = bin_dir / ("version-alt" if scenario == "mixed-patch" and host == "cp1" else "version")
        host_lines.append(f"        {host}: {{ansible_connection: local, restore_host_ocp_version_command: {command}}}\n")
    inventory.write_text("all:\n  children:\n    control_plane:\n      hosts:\n" + "".join(host_lines))
    # The playbook deliberately accepts controller state only below HOME. Keep
    # that real non-root-controller route in the disposable test too.
    controller_root = pathlib.Path(tempfile.mkdtemp(prefix="ocp-etcd-restore-fixture-", dir=pathlib.Path.home()))
    state = controller_root / "state"
    oc_path = bin_dir / "oc"
    if scenario == "missing-oc":
        oc_path = bin_dir / "missing-oc"
    args = [
        "ansible-playbook", "-i", str(inventory), str(PLAYBOOK),
        "-e", "restore_mode=apply", "-e", f"restore_selection={SET_ID}",
        "-e", "expected_cluster_id=fixture", "-e", "destructive_confirm=RESTORE_fixture",
        "-e", f"backup_source_hostname={hosts[-1]}", "-e", "recovery_host=cp0",
        "-e", f"restore_topology={'sno' if scenario == 'sno' else 'ha'}",
        "-e", f"backup_user_home={home}", "-e", f"backup_root={home / 'etcd-backups'}",
        "-e", f"restore_state_dir=/var/lib/ocp-etcd-restore/{fixture.name}",
        "-e", f"restore_controller_state_dir={state}", "-e", f"post_api_kubeconfig={kubeconfig}",
        "-e", f"restore_validator_path={validator}",
        "-e", f"restore_vendor_restore_script={bin_dir / 'restore'}",
        "-e", f"restore_vendor_disable_script={bin_dir / 'disable'}",
        "-e", f"restore_crictl_path={bin_dir / 'crictl'}", "-e", f"restore_etcd_manifest_path={manifest}",
        "-e", f"restore_etcd_cert_path={certs}", "-e", f"restore_oc_path={oc_path}",
        "-e", f"restore_fixture_action_path={bin_dir / 'action'}", "-e", "restore_min_free_kb=1",
        "-e", "restore_fence_retries=1", "-e", "restore_fence_delay=0",
    ]
    if len(hosts) == 3:
        args.extend(["-e", '{"ha_machine_map":{"cp1":"machine-1","cp2":"machine-2"}}'])
    if scenario == "check":
        args.insert(1, "--check")
    if scenario == "rerun":
        state.mkdir(parents=True)
        (state / f"{SET_ID}.json").write_text('{"phase":"FENCED"}')
    return fixture, controller_root, trace, args


def require(condition: bool, message: object) -> None:
    if not condition:
        raise AssertionError(message)


def run(scenario: str) -> None:
    fixture, controller_root, trace, args = build_fixture(scenario)
    try:
        result = subprocess.run(args, text=True, capture_output=True)
        output = result.stdout + result.stderr
        lines = trace.read_text().splitlines()
        state_file = controller_root / "state" / f"{SET_ID}.json"
        if scenario in {"ha", "sno"}:
            require(result.returncode == 0, output)
            require(output.count("Fetch selected set from the single source") == 1, output)
            require(lines.count(f"validate:{SET_ID}") == 2, lines)
            require(lines.count("vendor-restore") == 1, lines)
            require(lines.index("vendor-restore") < next(i for i, line in enumerate(lines) if line.startswith("oc:")), lines)
            source = "cp2" if scenario == "ha" else "cp0"
            require(lines[-2:] == [f"fresh-backup:{source}", f"timer-enable:{source}"], lines)
            if scenario == "ha":
                require(lines.count("fence") == 3, lines)
                require(max(i for i, line in enumerate(lines) if line == "fence") < lines.index("vendor-restore"), lines)
                first, second = (next(i for i, line in enumerate(lines) if f"machine-{n}" in line) for n in (1, 2))
                require(any("wait-for-stable-cluster" in line for line in lines[first + 1:second]), lines)
            else:
                require("fence" not in lines, lines)
            require(json.loads(state_file.read_text())["phase"] == "VERIFIED", state_file.read_text())
        elif scenario == "missing-oc":
            require(result.returncode != 0 and "executable absolute restore_oc_path" in output, output)
            require(not lines and not state_file.exists(), (lines, state_file.exists()))
        elif scenario in {"fence-fail", "container"}:
            require(result.returncode != 0 and "FENCING_INCOMPLETE" in output, output)
            require("vendor-restore" not in lines and "oc:" not in "\n".join(lines), lines)
            require(json.loads(state_file.read_text())["phase"] == "FENCING_INCOMPLETE", state_file.read_text())
        elif scenario == "rerun":
            require(result.returncode != 0 and "RESTORE_RERUN_REFUSED" in output, output)
            require(lines == [f"validate:{SET_ID}"], lines)
        elif scenario == "check":
            require(result.returncode == 0, output)
            require(lines == [f"validate:{SET_ID}"], lines)
            require(not state_file.exists(), state_file.exists())
        elif scenario in {"mixed-patch", "unknown-minor", "selected-mismatch"}:
            require(result.returncode != 0, output)
            require("Stop backup timers on all hosts" not in output, output)
            require("vendor-restore" not in lines and "fence" not in lines, lines)
            require(not state_file.exists(), state_file.exists())
            if scenario == "selected-mismatch":
                require("Selected set does not match cluster identity and installed exact z-stream" in output, output)
                require(lines == [f"validate:{SET_ID}"], lines)
            else:
                require("Installed versions must be equal OCP 4.20.z" in output, output)
                require(not lines, lines)
        print(f"SIMULATED_ACTUAL_RESTORE_{scenario.upper()}_OK")
        print("\n".join(lines))
    finally:
        shutil.rmtree(fixture, ignore_errors=True)
        shutil.rmtree(controller_root, ignore_errors=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=["ha", "sno", "missing-oc", "fence-fail", "container", "rerun", "check", "mixed-patch", "unknown-minor", "selected-mismatch"])
    run(parser.parse_args().scenario)
