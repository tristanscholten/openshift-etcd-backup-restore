import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNNER = ROOT / 'roles/etcd_backup/files/ocp-etcd-backup-runner'
VALIDATOR = ROOT / 'scripts/validate_backup.py'

class BackupHarnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.root = self.tmp / 'backups'; self.root.mkdir()
        self.runner = self.tmp / 'runner'
        text = RUNNER.read_text().replace('[[ $root == /home/core/* && $root != *\'..\'* ]]', '[[ $root == /* && $root != *\'..\'* ]]')
        self.runner.write_text(text); self.runner.chmod(0o700)
        self.vendor = self.tmp / 'vendor'
        self.vendor.write_text('#!/bin/sh\nset -eu\nd=$1; echo marker > "$d/snapshot_vendor.db"; echo keys > "$d/static_kuberesources_vendor.tar.gz"\n')
        self.vendor.chmod(0o700)
        self.env = os.environ | {'OCP_ETCD_BACKUP_VERSION':'4.20.3','OCP_ETCD_BACKUP_CLUSTER_ID':'fixture'}

    def tearDown(self): shutil.rmtree(self.tmp)

    def run_backup(self):
        return subprocess.run([str(self.runner), str(self.root), str(self.vendor)], env=self.env, text=True, capture_output=True)

    def test_five_successes_retain_three_complete_pairs(self):
        # Different second-level IDs are part of normal six-hour operation.
        for _ in range(5):
            result = self.run_backup(); self.assertEqual(result.returncode, 0, result.stderr)
            # The production schedule is six-hourly; sleep avoids same-second fixture IDs.
            import time; time.sleep(1)
        complete = sorted(self.root.glob('set-*/COMPLETE'))
        self.assertEqual(len(complete), 3)
        for marker in complete:
            set_dir = marker.parent
            self.assertTrue((set_dir / 'snapshot_vendor.db').is_file())
            self.assertTrue((set_dir / 'static_kuberesources_vendor.tar.gz').is_file())

    def test_failed_vendor_preserves_complete_sets(self):
        import time
        for _ in range(3):
            self.assertEqual(self.run_backup().returncode, 0)
            time.sleep(1)
        before = sorted(p.name for p in self.root.glob('set-*'))
        self.vendor.write_text('#!/bin/sh\nexit 1\n'); self.vendor.chmod(0o700)
        self.assertNotEqual(self.run_backup().returncode, 0)
        self.assertEqual(before, sorted(p.name for p in self.root.glob('set-*')))

    def test_validator_rejects_corrupt_pair(self):
        self.assertEqual(self.run_backup().returncode, 0)
        set_dir = next(self.root.glob('set-*'))
        (set_dir / 'snapshot_vendor.db').write_text('corrupt')
        result = subprocess.run(['python3', str(VALIDATOR), str(self.root), set_dir.name], text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)

if __name__ == '__main__': unittest.main()
