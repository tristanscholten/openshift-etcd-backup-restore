#!/usr/bin/env python3
"""SIMULATED disposable local-host execution of the actual restore playbook."""
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys

root = pathlib.Path(__file__).resolve().parents[1]
fixture = root / "tests" / ".actual-restore-fixture-run298"
if fixture.exists():
    shutil.rmtree(fixture)
backup = fixture / "home" / "etcd-backups" / "set-20261015T000000Z"
bin_dir = fixture / "bin"
backup.mkdir(parents=True)
bin_dir.mkdir()
set_id = backup.name
snapshot = "snapshot_20261015T000000Z.db"
static = "static_kuberesources_20261015T000000Z.tar.gz"
(backup / snapshot).write_text("snapshot-marker\n")
(backup / static).write_text("static-marker\n")
(backup / "SHA256SUMS").write_text("".join(f"{hashlib.sha256((backup / n).read_bytes()).hexdigest()}  {n}\n" for n in (snapshot, static)))
(backup / "metadata.json").write_text(json.dumps({"schema": 1, "set_id": set_id, "timestamp": "20261015T000000Z", "ocp_version": "4.20.3", "cluster_id": "fixture", "snapshot": snapshot, "static_resources": static}))
(backup / "COMPLETE").write_text("")
shutil.copy(root / "scripts" / "validate_backup.py", bin_dir / "validator")
trace = fixture / "trace"
trace.write_text("")
for name, content in {
    "version": '#!/bin/sh\nprintf "4.20.3\\n"\n',
    "crictl": '#!/bin/sh\nexit 0\n',
    "restore": f'#!/bin/sh\nprintf "vendor-restore\\n" >> "{trace}"\n',
    "disable": f'#!/bin/sh\nprintf "fence\\n" >> "{trace}"\n',
    "oc": f'#!/bin/sh\nprintf "oc:%s\\n" "$*" >> "{trace}"\n',
    "action": f'#!/bin/sh\nprintf "%s:%s\\n" "$1" "$2" >> "{trace}"\n',
}.items():
    path = bin_dir / name
    path.write_text(content)
    path.chmod(0o700)
(bin_dir / "validator").chmod(0o700)
certs = fixture / "certs"
certs.mkdir()
os.utime(certs, (1, 1))
(fixture / "manifest").write_text("manifest\n")
(fixture / "manifest").chmod(0o700)
(fixture / "kubeconfig").write_text("apiVersion: v1\nclusters: []\n")
(fixture / "inventory.yml").write_text("""all:
  children:
    control_plane:
      hosts:
        cp0: {ansible_connection: local}
        cp1: {ansible_connection: local}
        cp2: {ansible_connection: local}
""")
state = fixture / "controller-state"
env = os.environ | {"TRACE": str(trace)}
args = [
    "ansible-playbook", "-i", str(fixture / "inventory.yml"), str(root / "playbooks" / "restore.yml"),
    "-e", "restore_mode=apply", "-e", f"restore_selection={set_id}", "-e", "expected_cluster_id=fixture",
    "-e", "destructive_confirm=RESTORE_fixture", "-e", "backup_source_hostname=cp1", "-e", "recovery_host=cp0", "-e", "restore_topology=ha",
    "-e", f"backup_user_home={fixture / 'home'}", "-e", f"backup_root={fixture / 'home' / 'etcd-backups'}",
    "-e", f"restore_state_dir=/var/lib/ocp-etcd-restore/fixture-run298", "-e", f"restore_controller_state_dir={state}",
    "-e", f"post_api_kubeconfig={fixture / 'kubeconfig'}", "-e", '{"ha_machine_map":{"cp1":"machine-1","cp2":"machine-2"}}',
    "-e", f"ocp_version_command={bin_dir / 'version'}", "-e", f"restore_validator_path={bin_dir / 'validator'}",
    "-e", f"restore_vendor_restore_script={bin_dir / 'restore'}", "-e", f"restore_vendor_disable_script={bin_dir / 'disable'}",
    "-e", f"restore_crictl_path={bin_dir / 'crictl'}", "-e", f"restore_etcd_manifest_path={fixture / 'manifest'}", "-e", f"restore_etcd_cert_path={certs}",
    "-e", f"restore_oc_path={bin_dir / 'oc'}", "-e", f"restore_fixture_action_path={bin_dir / 'action'}", "-e", "restore_min_free_kb=1",
]
result = subprocess.run(args, text=True, capture_output=True, env=env)
print(result.stdout)
print(result.stderr, file=sys.stderr)
if result.returncode:
    raise SystemExit(result.returncode)
lines = trace.read_text().splitlines()
assert lines.count("vendor-restore") == 1, lines
assert lines.count("fence") == 3, lines
assert max(i for i, x in enumerate(lines) if x == "fence") < lines.index("vendor-restore"), lines
assert lines.index("vendor-restore") < next(i for i, x in enumerate(lines) if x.startswith("oc:")), lines
assert lines[-2:] == ["fresh-backup:cp1", "timer-enable:cp1"], lines
print("SIMULATED_ACTUAL_RESTORE_PLAYBOOK_TRACE_OK")
print("\n".join(lines))
