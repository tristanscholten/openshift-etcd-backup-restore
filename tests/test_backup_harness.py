import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNNER = ROOT / 'roles/etcd_backup/files/ocp-etcd-backup-runner'
VALIDATOR = ROOT / 'scripts/validate_backup.py'
RESTORE = ROOT / 'playbooks/restore.yml'
LIST = ROOT / 'playbooks/list-backups.yml'
UNINSTALL = ROOT / 'playbooks/uninstall.yml'
HA_PEER = ROOT / 'roles/restore_ha_peer/tasks/main.yml'
SERVICE = ROOT / 'roles/etcd_backup/templates/ocp-etcd-backup.service.j2'
ENV_TEMPLATE = ROOT / 'roles/etcd_backup/templates/ocp-etcd-backup.env.j2'
ACTUAL_FIXTURE = ROOT / 'tests/run_actual_restore_fixture.py'
ACTUAL_LIST_FIXTURE = ROOT / 'tests/run_actual_list_fixture.py'


class BackupHarnessTests(unittest.TestCase):
    """SIMULATED vendor fixture; it is not OpenShift DR proof."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.home = self.tmp / 'core'; self.home.mkdir()
        self.root = self.home / 'etcd-backups'; self.root.mkdir()
        self.validator = self.tmp / 'validate_backup.py'
        shutil.copy(VALIDATOR, self.validator); self.validator.chmod(0o700)
        self.vendor = self.tmp / 'vendor'
        self.vendor.write_text('#!/bin/sh\nset -eu\nd=$1; echo marker > "$d/snapshot_$(date -u +%Y%m%dT%H%M%SZ).db"; f=$(find "$d" -name "snapshot_*.db" -printf "%f"); t=${f#snapshot_}; t=${t%.db}; echo keys > "$d/static_kuberesources_$t.tar.gz"\n')
        self.vendor.chmod(0o700)
        self.capture = self.tmp / 'capture'; self.capture.write_text('#!/bin/sh\nexit 99\n'); self.capture.chmod(0o700)
        self.env = {'PATH': '/usr/bin:/bin', 'OCP_ETCD_BACKUP_VERSION': '4.20.3', 'OCP_ETCD_BACKUP_CLUSTER_ID': 'fixture', 'OCP_ETCD_BACKUP_USER_HOME': str(self.home), 'OCP_ETCD_BACKUP_TOPOLOGY': 'sno'}

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_backup(self):
        return subprocess.run([str(RUNNER), str(self.root), str(self.vendor), str(self.validator), str(self.capture)], env=self.env, text=True, capture_output=True)

    def test_five_successes_retain_three_complete_pairs(self):
        for _ in range(5):
            result = self.run_backup(); self.assertEqual(result.returncode, 0, result.stderr)
            time.sleep(1.1)
        complete = sorted(self.root.glob('set-*/COMPLETE'))
        self.assertEqual(len(complete), 3)
        for marker in complete:
            data = json.loads((marker.parent / 'metadata.json').read_text())
            self.assertTrue((marker.parent / data['snapshot']).is_file())
            self.assertTrue((marker.parent / data['static_resources']).is_file())

    def test_failed_vendor_preserves_three_complete_sets(self):
        for _ in range(3):
            self.assertEqual(self.run_backup().returncode, 0); time.sleep(1.1)
        before = sorted(path.name for path in self.root.glob('set-*'))
        self.vendor.write_text('#!/bin/sh\nexit 1\n'); self.vendor.chmod(0o700)
        self.assertNotEqual(self.run_backup().returncode, 0)
        self.assertEqual(before, sorted(path.name for path in self.root.glob('set-*')))

    def test_failed_ha_capture_preserves_three_complete_sets(self):
        for _ in range(3):
            self.assertEqual(self.run_backup().returncode, 0); time.sleep(1.1)
        before = sorted(path.name for path in self.root.glob('set-*'))
        ha_env = dict(self.env, OCP_ETCD_BACKUP_TOPOLOGY='ha')
        result = subprocess.run([str(RUNNER), str(self.root), str(self.vendor), str(self.validator), str(self.capture)], env=ha_env, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(before, sorted(path.name for path in self.root.glob('set-*')))

    def test_corrupt_retention_candidate_fails_closed(self):
        for _ in range(3):
            self.assertEqual(self.run_backup().returncode, 0); time.sleep(1.1)
        old = sorted(self.root.glob('set-*'))[0]
        data = json.loads((old / 'metadata.json').read_text())
        (old / data['snapshot']).write_text('bad')
        before = sorted(path.name for path in self.root.glob('set-*'))
        self.assertNotEqual(self.run_backup().returncode, 0)
        self.assertEqual(before, sorted(path.name for path in self.root.glob('set-*')))

    def test_overlap_lock_refuses_second_runner(self):
        lock = self.tmp / 'lock'
        held = subprocess.Popen(['/usr/bin/flock', '-n', str(lock), '/bin/sleep', '1'])
        time.sleep(0.1)
        self.assertNotEqual(subprocess.run(['/usr/bin/flock', '-n', str(lock), '/bin/true']).returncode, 0)
        held.wait()

    def test_validator_excludes_corrupt_candidate(self):
        self.assertEqual(self.run_backup().returncode, 0)
        valid = next(self.root.glob('set-*'))
        self.assertEqual(subprocess.run([str(self.validator), str(self.root), valid.name], capture_output=True).returncode, 0)
        data = json.loads((valid / 'metadata.json').read_text())
        (valid / data['snapshot']).write_text('corrupt')
        self.assertNotEqual(subprocess.run([str(self.validator), str(self.root), valid.name], capture_output=True).returncode, 0)


class RestoreContractTests(unittest.TestCase):
    """SIMULATED multi-host restore execution; no cluster is contacted."""

    def test_actual_ansible_wrong_mapping_stops_before_mutation(self):
        inventory = ROOT / 'tests' / 'fixture-invalid-ha.yml'
        inventory.write_text('''all:\n  children:\n    control_plane:\n      hosts:\n        cp0: {ansible_connection: local}\n        cp1: {ansible_connection: local}\n        cp2: {ansible_connection: local}\n''')
        try:
            result = subprocess.run([
                'ansible-playbook', '-i', str(inventory), str(RESTORE),
                '-e', 'ansible_become=false',
                '-e', 'restore_mode=apply', '-e', 'restore_selection=set-20261015T000000Z',
                '-e', 'expected_cluster_id=fixture', '-e', 'destructive_confirm=RESTORE_fixture',
                '-e', 'backup_source_hostname=cp0', '-e', 'recovery_host=cp0', '-e', 'restore_topology=ha',
                '-e', 'post_api_kubeconfig=/private/fixture',
                '-e', '{"ha_machine_map":{"unrelated-a":"machine-a","unrelated-b":"machine-b"}}',
            ], text=True, capture_output=True)
        finally:
            inventory.unlink(missing_ok=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Unsupported topology, exact non-recovery Machine mapping', result.stdout + result.stderr)
        self.assertNotIn('Stop backup timers on all hosts', result.stdout + result.stderr)

    def test_actual_ansible_missing_kubeconfig_stops_before_mutation(self):
        inventory = ROOT / 'tests' / 'fixture-missing-kubeconfig-ha.yml'
        inventory.write_text('''all:\n  children:\n    control_plane:\n      hosts:\n        cp0: {ansible_connection: local}\n        cp1: {ansible_connection: local}\n        cp2: {ansible_connection: local}\n''')
        missing_kubeconfig = ROOT / 'tests' / 'does-not-exist.kubeconfig'
        try:
            result = subprocess.run([
                'ansible-playbook', '-i', str(inventory), str(RESTORE),
                '-e', 'ansible_become=false',
                '-e', 'restore_mode=apply', '-e', 'restore_selection=set-20261015T000000Z',
                '-e', 'expected_cluster_id=fixture', '-e', 'destructive_confirm=RESTORE_fixture',
                '-e', 'backup_source_hostname=cp0', '-e', 'recovery_host=cp0', '-e', 'restore_topology=ha',
                '-e', f'post_api_kubeconfig={missing_kubeconfig}',
                '-e', '{"ha_machine_map":{"cp1":"machine-1","cp2":"machine-2"}}',
            ], text=True, capture_output=True)
        finally:
            inventory.unlink(missing_ok=True)
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Apply requires an existing readable regular certificate kubeconfig', output)
        self.assertNotIn('Stop backup timers on all hosts', output)
        self.assertNotIn('Fence every HA peer with vendor command', output)

    def test_playbooks_have_executable_guardrail_shapes(self):
        restore, listing, uninstall, peer = (p.read_text() for p in (RESTORE, LIST, UNINSTALL, HA_PEER))
        self.assertIn('ha_machine_map.keys()', restore)
        self.assertIn('Recreate each mapped HA machine and wait before next', restore)
        self.assertIn('Validate every candidate without reading archive content', listing)
        self.assertIn('backup_uninstall_confirm', uninstall)
        self.assertIn('Persist peer recovery failure checkpoint', peer)

    def test_runtime_configuration_templates_supply_required_variables(self):
        env, unit = ENV_TEMPLATE.read_text(), SERVICE.read_text()
        for key in ('OCP_ETCD_BACKUP_USER_HOME', 'OCP_ETCD_BACKUP_VERSION', 'OCP_ETCD_BACKUP_CLUSTER_ID'):
            self.assertIn(key, env)
        self.assertIn('EnvironmentFile={{ backup_environment_file | quote }}', unit)

    def test_actual_list_playbook_excludes_corrupt_candidate(self):
        result = subprocess.run([sys.executable, str(ACTUAL_LIST_FIXTURE)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('SIMULATED_ACTUAL_LIST_PLAYBOOK_OK', result.stdout)

    def test_actual_playbook_version_refusal_matrix(self):
        for scenario in ('mixed-patch', 'unknown-minor', 'selected-mismatch'):
            result = subprocess.run([sys.executable, str(ACTUAL_FIXTURE), scenario], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(f'SIMULATED_ACTUAL_RESTORE_{scenario.upper()}_OK', result.stdout)

    def test_actual_playbook_simulated_success_matrix(self):
        for scenario in ('ha', 'sno'):
            result = subprocess.run([sys.executable, str(ACTUAL_FIXTURE), scenario], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(f'SIMULATED_ACTUAL_RESTORE_{scenario.upper()}_OK', result.stdout)

    def test_actual_playbook_simulated_fail_closed_matrix(self):
        for scenario in ('missing-oc', 'fence-fail', 'container', 'rerun', 'check'):
            result = subprocess.run([sys.executable, str(ACTUAL_FIXTURE), scenario], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(f'SIMULATED_ACTUAL_RESTORE_{scenario.upper()}_OK', result.stdout)


if __name__ == '__main__':
    unittest.main()
