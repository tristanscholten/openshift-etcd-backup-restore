import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'manifests' / 'etcd-backup-cronjob.yaml'


class CronJobManifestTests(unittest.TestCase):
    """Disposable host-script fixture; not a real OpenShift validation."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.host = self.tmp / 'host'
        self.backups = self.host / 'home/core/etcd-backups'
        self.backups.mkdir(parents=True)
        vendor = self.host / 'usr/local/bin/cluster-backup.sh'
        vendor.parent.mkdir(parents=True)
        vendor.write_text(
            '#!/bin/sh\n'
            'set -eu\n'
            'd=$1\n'
            'if [ "${FIXTURE_MODE:-success}" = partial ]; then\n'
            '  printf partial > "$d/snapshot_${FIXTURE_TIMESTAMP}.db"\n'
            '  exit 42\n'
            'fi\n'
            'printf snapshot > "$d/snapshot_${FIXTURE_TIMESTAMP}.db"\n'
            'printf resources > "$d/static_kuberesources_${FIXTURE_TIMESTAMP}.tar.gz"\n'
        )
        vendor.chmod(0o700)
        self.bin = self.tmp / 'bin'
        self.bin.mkdir()
        chroot = self.bin / 'chroot'
        chroot.write_text(
            '#!/bin/sh\nset -eu\nroot=$1\nshift\ntarget=$1\nshift\nif [ "$target" = /usr/bin/oc ]; then\n  case "$*" in *spec.clusterID*) printf 11111111-1111-1111-1111-111111111111;; *) printf 4.20.3;; esac\n  exit 0\nfi\nif [ "$target" = /usr/bin/sha256sum ]; then\n  sha256sum "$root$1"\n  exit 0\nfi\nif [ "$target" = /usr/local/bin/cluster-backup.sh ]; then\n  exec "$root$target" "$root$1"\nfi\nexec "$root$target" "$@"\n'
        )
        chroot.chmod(0o700)
        docs = list(yaml.safe_load_all(MANIFEST.read_text()))
        cronjob = next(item for item in docs if item['kind'] == 'CronJob')
        self.script = cronjob['spec']['jobTemplate']['spec']['template']['spec']['containers'][0]['args'][0]

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_script(self, timestamp, mode='success'):
        env = dict(os.environ, PATH=f'{self.bin}:{os.environ["PATH"]}', HOST_ROOT=str(self.host), FIXTURE_TIMESTAMP=timestamp, FIXTURE_MODE=mode)
        return subprocess.run(['/bin/bash', '-ceu', self.script], cwd=self.tmp, env=env, text=True, capture_output=True)

    def complete_timestamps(self):
        return sorted(
            marker.name.removeprefix('.ocp-etcd-cronjob-managed-').removesuffix('.json')
            for marker in self.backups.glob('.ocp-etcd-cronjob-managed-*.json')
            if (self.backups / f'snapshot_{marker.name.removeprefix(".ocp-etcd-cronjob-managed-").removesuffix(".json")}.db').is_file()
            and (self.backups / f'static_kuberesources_{marker.name.removeprefix(".ocp-etcd-cronjob-managed-").removesuffix(".json")}.tar.gz').is_file()
        )

    def test_manifest_has_bounded_single_node_privileged_cronjob(self):
        docs = list(yaml.safe_load_all(MANIFEST.read_text()))
        kinds = [item['kind'] for item in docs]
        self.assertEqual(kinds, ['Namespace', 'ServiceAccount', 'ClusterRole', 'ClusterRoleBinding', 'CronJob'])
        cronjob = docs[-1]['spec']
        self.assertEqual(cronjob['schedule'], '0 */6 * * *')
        self.assertEqual(cronjob['timeZone'], 'UTC')
        self.assertEqual(cronjob['concurrencyPolicy'], 'Forbid')
        self.assertEqual(cronjob['jobTemplate']['spec']['backoffLimit'], 1)
        self.assertEqual(cronjob['jobTemplate']['spec']['activeDeadlineSeconds'], 1800)
        pod = cronjob['jobTemplate']['spec']['template']['spec']
        self.assertEqual(pod['nodeSelector']['kubernetes.io/hostname'], 'REPLACE_WITH_ONE_CONTROL_PLANE_HOSTNAME')
        self.assertFalse(pod['automountServiceAccountToken'])
        self.assertTrue(pod['hostNetwork'])
        self.assertNotIn('hostPID', pod)
        self.assertNotIn('hostIPC', pod)
        container = pod['containers'][0]
        self.assertTrue(container['securityContext']['privileged'])
        self.assertEqual(container['volumeMounts'][0]['mountPath'], '/host')
        self.assertIn('chroot "$host_root" /usr/local/bin/cluster-backup.sh /home/core/etcd-backups', self.script)

    def test_exact_embedded_script_retains_three_and_preserves_unmanaged_files(self):
        unrelated = self.backups / 'do-not-delete'
        unrelated.write_text('unmanaged')
        for timestamp in ('20261015T000000Z', '20261015T060000Z', '20261015T120000Z', '20261015T180000Z'):
            result = self.run_script(timestamp)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.complete_timestamps(), ['20261015T060000Z', '20261015T120000Z', '20261015T180000Z'])
        self.assertEqual(unrelated.read_text(), 'unmanaged')
        self.assertFalse((self.backups / 'snapshot_20261015T000000Z.db').exists())

    def test_failed_partial_vendor_leaves_prior_three_and_unrelated_files(self):
        for timestamp in ('20261015T000000Z', '20261015T060000Z', '20261015T120000Z'):
            self.assertEqual(self.run_script(timestamp).returncode, 0)
        unrelated = self.backups / 'unmanaged-snapshot.db'
        unrelated.write_text('unmanaged')
        result = self.run_script('20261015T180000Z', mode='partial')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.complete_timestamps(), ['20261015T000000Z', '20261015T060000Z', '20261015T120000Z'])
        self.assertEqual(unrelated.read_text(), 'unmanaged')
        self.assertTrue((self.backups / 'snapshot_20261015T180000Z.db').is_file())
        self.assertFalse((self.backups / '.ocp-etcd-cronjob-managed-20261015T180000Z.json').exists())


if __name__ == '__main__':
    unittest.main()
