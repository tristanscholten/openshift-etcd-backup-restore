import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNNER = ROOT / 'roles/etcd_backup/files/ocp-etcd-backup-runner'
VALIDATOR = ROOT / 'scripts/validate_backup.py'
RESTORE = ROOT / 'playbooks/restore.yml'


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
        self.env = os.environ | {'OCP_ETCD_BACKUP_VERSION': '4.20.3', 'OCP_ETCD_BACKUP_CLUSTER_ID': 'fixture', 'OCP_ETCD_BACKUP_USER_HOME': str(self.home)}

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_backup(self):
        return subprocess.run([str(RUNNER), str(self.root), str(self.vendor), str(self.validator)], env=self.env, text=True, capture_output=True)

    def test_five_successes_retain_three_complete_pairs(self):
        for _ in range(5):
            result = self.run_backup(); self.assertEqual(result.returncode, 0, result.stderr)
            time.sleep(1)
        complete = sorted(self.root.glob('set-*/COMPLETE'))
        self.assertEqual(len(complete), 3)
        for marker in complete:
            data = json.loads((marker.parent / 'metadata.json').read_text())
            self.assertTrue((marker.parent / data['snapshot']).is_file())
            self.assertTrue((marker.parent / data['static_resources']).is_file())

    def test_failed_vendor_preserves_three_complete_sets(self):
        for _ in range(3):
            self.assertEqual(self.run_backup().returncode, 0); time.sleep(1)
        before = sorted(path.name for path in self.root.glob('set-*'))
        self.vendor.write_text('#!/bin/sh\nexit 1\n'); self.vendor.chmod(0o700)
        self.assertNotEqual(self.run_backup().returncode, 0)
        self.assertEqual(before, sorted(path.name for path in self.root.glob('set-*')))

    def test_corrupt_retention_candidate_fails_closed(self):
        for _ in range(3):
            self.assertEqual(self.run_backup().returncode, 0); time.sleep(1)
        old = sorted(self.root.glob('set-*'))[0]
        data = json.loads((old / 'metadata.json').read_text())
        (old / data['snapshot']).write_text('bad')
        before = sorted(path.name for path in self.root.glob('set-*'))
        self.assertNotEqual(self.run_backup().returncode, 0)
        self.assertEqual(before, sorted(path.name for path in self.root.glob('set-*')))

    def test_overlap_lock_refuses_second_runner(self):
        lock = self.tmp / 'lock'
        held = subprocess.Popen(['/usr/bin/flock', '-n', str(lock), '/bin/sleep', '2'])
        time.sleep(0.1)
        result = subprocess.run(['/usr/bin/flock', '-n', str(lock), '/bin/true'])
        held.wait()
        self.assertNotEqual(result.returncode, 0)


class RestoreContractTests(unittest.TestCase):
    """SIMULATED multi-host ordering contract; no cluster is contacted."""

    def setUp(self):
        self.text = RESTORE.read_text()

    def test_source_copy_precedes_all_host_fence_and_one_vendor_restore(self):
        self.assertIn("delegate_to: '{{ backup_source_hostname }}'", self.text)
        self.assertLess(self.text.index('Fetch selected set from the single source'), self.text.index('Fence every HA peer'))
        self.assertLess(self.text.index('Verify copied source set hash gate'), self.text.index('Fence every HA peer'))
        self.assertEqual(self.text.count('cluster-restore.sh'), 2)  # stat check plus one invocation
        self.assertIn("when: inventory_hostname == recovery_host", self.text)

    def test_fail_closed_fence_rerun_checkmode_and_version_guards(self):
        for fragment in ['RESTORE_RERUN_REFUSED', 'retries: 30', "restore_etcd_fence.stdout | trim == ''", "restore_mode == 'preflight' or ansible_check_mode", "^4\\.20\\.[0-9]+$"]:
            self.assertIn(fragment, self.text)
        self.assertLess(self.text.index('Wait for API only after vendor restore'), self.text.index('Enable temporary quorum guard after API recovery'))
        self.assertNotIn('oc, --kubeconfig', self.text[:self.text.index('Wait for API only after vendor restore')])

    def test_ha_prerequisites_are_checked_before_timer_pause(self):
        self.assertLess(self.text.index('Validate all topology prerequisites before mutation'), self.text.index('Stop backup timers on all hosts'))
        self.assertIn('ha_machine_map | length == 2', self.text)
        self.assertIn('Recreate mapped HA machines one at a time', self.text)


if __name__ == '__main__':
    unittest.main()
