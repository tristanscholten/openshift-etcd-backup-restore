#!/usr/bin/env python3
"""Actual list playbook over flat CronJob-style disposable pairs."""
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLAYBOOK = ROOT / 'playbooks/list-backups.yml'
STAMP = '20261015T000000Z'
PREFIX = '.ocp-etcd-cronjob-managed-'


def pair(root, timestamp, corrupt=False):
    root.mkdir(parents=True, exist_ok=True)
    snapshot = root / f'snapshot_{timestamp}.db'; resources = root / f'static_kuberesources_{timestamp}.tar.gz'
    snapshot.write_text('snapshot'); resources.write_text('resources')
    data = {'schema': 1, 'timestamp': timestamp, 'snapshot': snapshot.name, 'static_resources': resources.name,
            'cluster_id': '11111111-1111-1111-1111-111111111111', 'ocp_version': '4.20.3',
            'snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(), 'static_resources_sha256': hashlib.sha256(resources.read_bytes()).hexdigest(),
            'topology': 'sno', 'cpms_enabled': False, 'control_plane': [{'node_name': 'source', 'system_uuid': '22222222-2222-2222-2222-222222222222', 'machine_config': 'rendered-master-0'}]}
    (root / f'{PREFIX}{timestamp}.json').write_text(json.dumps(data))
    if corrupt: snapshot.write_text('corrupt')


def main():
    fixture = pathlib.Path(tempfile.mkdtemp())
    try:
        root = fixture / 'backups'; pair(root, STAMP); pair(root, '20261015T060000Z', True)
        inventory = fixture / 'inventory.yml'; inventory.write_text('all:\n  children:\n    control_plane:\n      hosts:\n        source: {ansible_connection: local}\n')
        result = subprocess.run(['ansible-playbook', '-i', str(inventory), str(PLAYBOOK), '-e', 'ansible_become=false', '-e', 'backup_source_hostname=source', '-e', f'backup_root={root}'], text=True, capture_output=True)
        output = result.stdout + result.stderr
        if result.returncode or STAMP not in output or '20261015T060000Z' in output: raise AssertionError(output)
        print('SIMULATED_ACTUAL_LIST_PLAYBOOK_OK')
    finally: shutil.rmtree(fixture, ignore_errors=True)

if __name__ == '__main__': main()
