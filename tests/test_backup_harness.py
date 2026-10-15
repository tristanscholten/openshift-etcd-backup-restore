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
ACTUAL_FIXTURE = ROOT / 'tests/run_actual_restore_fixture.py'
ACTUAL_LIST_FIXTURE = ROOT / 'tests/run_actual_list_fixture.py'


class BackupHarnessTests(unittest.TestCase):
    """Legacy managed-backup regression fixture; no OpenShift cluster."""
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()); self.home = self.tmp / 'core'; self.home.mkdir()
        self.root = self.home / 'etcd-backups'; self.root.mkdir(); self.vendor = self.tmp / 'vendor'
        self.validator = self.tmp / 'validate_backup.py'; shutil.copy(VALIDATOR, self.validator); self.validator.chmod(0o700)
        self.vendor.write_text('#!/bin/sh\nset -eu\nd=$1; t=$(date -u +%Y%m%dT%H%M%SZ); echo snapshot > "$d/snapshot_$t.db"; echo keys > "$d/static_kuberesources_$t.tar.gz"\n'); self.vendor.chmod(0o700)
        self.capture = self.tmp / 'capture'; self.capture.write_text('#!/bin/sh\nexit 99\n'); self.capture.chmod(0o700)
        self.env = {'PATH': '/usr/bin:/bin', 'OCP_ETCD_BACKUP_VERSION': '4.20.3', 'OCP_ETCD_BACKUP_CLUSTER_ID': '11111111-1111-1111-1111-111111111111', 'OCP_ETCD_BACKUP_USER_HOME': str(self.home), 'OCP_ETCD_BACKUP_TOPOLOGY': 'sno'}
    def tearDown(self): shutil.rmtree(self.tmp)
    def run_backup(self): return subprocess.run([str(RUNNER), str(self.root), str(self.vendor), str(self.validator), str(self.capture)], env=self.env, text=True, capture_output=True)
    def test_retention_and_vendor_failure_preserve_managed_sets(self):
        for _ in range(4): self.assertEqual(self.run_backup().returncode, 0); time.sleep(1.1)
        self.assertEqual(len(list(self.root.glob('set-*/COMPLETE'))), 3)
        before = sorted(p.name for p in self.root.glob('set-*')); self.vendor.write_text('#!/bin/sh\nexit 1\n'); self.vendor.chmod(0o700)
        self.assertNotEqual(self.run_backup().returncode, 0); self.assertEqual(before, sorted(p.name for p in self.root.glob('set-*')))


class RestoreContractTests(unittest.TestCase):
    """Actual playbooks over local disposable stubs; not OpenShift DR proof."""
    def run_fixture(self, scenario):
        result = subprocess.run([sys.executable, str(ACTUAL_FIXTURE), scenario], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f'SIMULATED_ACTUAL_RESTORE_{scenario.upper().replace("-", "_")}_OK', result.stdout)
    def test_actual_cronjob_pair_to_sno_and_ha_restore(self):
        for scenario in ('sno', 'ha'): self.run_fixture(scenario)
    def test_fail_closed_before_vendor_restore_and_checkpoint_failures(self):
        for scenario in ('bad-pair', 'wrong-release', 'fence-fail', 'api-fail', 'rerun'): self.run_fixture(scenario)
    def test_actual_list_playbook_excludes_corrupt_candidate(self):
        result = subprocess.run([sys.executable, str(ACTUAL_LIST_FIXTURE)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('SIMULATED_ACTUAL_LIST_PLAYBOOK_OK', result.stdout)
    def test_restore_guardrail_shapes(self):
        restore, listing = RESTORE.read_text(), LIST.read_text()
        self.assertIn('discover_vendor_backup.py', restore)
        self.assertIn('get', restore); self.assertIn('--raw=/readyz', restore)
        self.assertIn('RESTORE_RERUN_REFUSED', restore); self.assertIn('Record failed recovery checkpoint', restore)
        self.assertNotIn('delete_machine', restore); self.assertIn('CronJob vendor pairs', listing)


if __name__ == '__main__': unittest.main()
