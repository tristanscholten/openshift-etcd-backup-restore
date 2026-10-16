#!/usr/bin/env python3
"""Disposable execution of actual CronJob shell and restore playbook; no cluster."""
import argparse
import errno
import getpass
import os
import pathlib
import pty
import select
import shutil
import subprocess
import tempfile
import time
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'manifests/etcd-backup-cronjob.yaml'
PLAYBOOK = ROOT / 'playbooks/restore.yml'
STAMP = '20261015T000000Z'
CLUSTER = '11111111-1111-1111-1111-111111111111'


def exe(path, body):
    path.write_text(body); path.chmod(0o700)


def create_backup(fixture, hosts):
    host = fixture / 'host'; backups = host / 'home/core/etcd-backups'
    backups.mkdir(parents=True); (host / 'usr/local/bin').mkdir(parents=True)
    exe(host / 'usr/local/bin/cluster-backup.sh', f'#!/bin/sh\nprintf snapshot > "$1/snapshot_{STAMP}.db"\nprintf resources > "$1/static_kuberesources_{STAMP}.tar.gz"\n')
    bindir = fixture / 'cron-bin'; bindir.mkdir()
    node_lines = ''.join(f'{name}|11111111-1111-1111-1111-1111111111{index:02}|rendered-master-{index}|Red Hat Enterprise Linux CoreOS 420.92.20261001\n' for index, name in enumerate(hosts))
    exe(bindir / 'chroot', f'''#!/bin/sh
root=$1; shift; target=$1; shift
if [ "$target" = /usr/bin/oc ]; then
  case "$*" in
    *controlplanemachineset*) echo NotFound >&2; exit 1;;
    *node-role.kubernetes.io/control-plane*) printf '{node_lines}'; exit 0;;
    *spec.clusterID*) printf {CLUSTER}; exit 0;;
    *) printf 4.20.3; exit 0;;
  esac
fi
if [ "$target" = /usr/bin/sha256sum ]; then sha256sum "$root$1"; exit 0; fi
if [ "$target" = /usr/local/bin/cluster-backup.sh ]; then exec "$root$target" "$root$1"; fi
exec "$root$target" "$@"
''')
    docs = list(yaml.safe_load_all(MANIFEST.read_text()))
    shell = next(d for d in docs if d['kind'] == 'CronJob')['spec']['jobTemplate']['spec']['template']['spec']['containers'][0]['args'][0]
    result = subprocess.run(['/bin/bash', '-ceu', shell], env=dict(os.environ, PATH=f'{bindir}:{os.environ["PATH"]}', HOST_ROOT=str(host)), text=True, capture_output=True)
    if result.returncode: raise AssertionError(result.stderr)
    return backups


def build(scenario):
    fixture = pathlib.Path(tempfile.mkdtemp(prefix='actual-restore-'))
    hosts = ['cp0'] if scenario == 'sno' else ['cp0', 'cp1', 'cp2']
    source = create_backup(fixture, hosts); trace = fixture / 'trace'; trace.write_text('')
    bindir = fixture / 'bin'; bindir.mkdir()
    exe(bindir / 'restore', f'#!/bin/sh\nprintf "restore\\n" >> "{trace}"\n')
    exe(bindir / 'disable', f'#!/bin/sh\nprintf "fence\\n" >> "{trace}"\n' + ('exit 18\n' if scenario == 'fence-fail' else ''))
    exe(bindir / 'oc', f'''#!/bin/sh
printf "oc:$*\\n" >> "{trace}"
case "$*" in *readyz*) {'exit 7' if scenario == 'api-fail' else 'exit 0'};; *) exit 0;; esac
''')
    identity_dir = fixture / 'identity'; config_dir = fixture / 'config'; os_release_dir = fixture / 'os-release'; identity_dir.mkdir(); config_dir.mkdir(); os_release_dir.mkdir()
    for index, name in enumerate(hosts):
        (identity_dir / name).write_text(f'11111111-1111-1111-1111-1111111111{index:02}\n')
        (config_dir / name).write_text('{"metadata":{"name":"rendered-master-' + str(index) + '"}}')
        (os_release_dir / name).write_text('PRETTY_NAME="Red Hat Enterprise Linux CoreOS 420.92.20261001"\n')
    if scenario == 'identity-mismatch': (identity_dir / 'cp0').write_text('22222222-2222-2222-2222-222222222222\\n')
    if scenario == 'release-mismatch': (config_dir / 'cp0').write_text('{"metadata":{"name":"rendered-master-wrong"}}')
    if scenario == 'rhcos-mismatch': (os_release_dir / 'cp0').write_text('PRETTY_NAME="Red Hat Enterprise Linux CoreOS 420.93.20261002"\n')
    if scenario == 'topology-mismatch': hosts = ['cp0', 'cp1', 'cp2']
    inventory = fixture / 'inventory.yml'
    inventory.write_text('all:\n  children:\n    control_plane:\n      hosts:\n' + ''.join(f'        {h}: {{ansible_connection: local}}\n' for h in hosts))
    kubeconfig = fixture / 'kubeconfig'; kubeconfig.write_text('apiVersion: v1\n')
    state = fixture / 'state'
    args = ['ansible-playbook', '-i', str(inventory), str(PLAYBOOK), '-e', 'ansible_become=false',
            '-e', f'backup_root={source}', '-e', f'backup_source_hostname={hosts[-1]}', '-e', 'recovery_host=cp0',
            '-e', f'restore_topology={"sno" if scenario in ("sno", "topology-mismatch") else "ha"}',
            '-e', f'expected_cluster_id={CLUSTER}', '-e', 'expected_ocp_version=4.20.3',
            '-e', f'post_api_kubeconfig={kubeconfig}',
            '-e', f'restore_oc_path={bindir / "oc"}', '-e', f'restore_vendor_restore_script={bindir / "restore"}',
            '-e', f'restore_vendor_disable_script={bindir / "disable"}', '-e', f'restore_controller_state_dir={state}',
            '-e', f'restore_stage_dir={fixture / "stage"}', '-e', f'restore_stage_owner={getpass.getuser()}', '-e', f'restore_stage_group={getpass.getuser()}',
            '-e', f'restore_host_identity_path={identity_dir}/{{{{ inventory_hostname }}}}', '-e', f'restore_machine_config_path={config_dir}/{{{{ inventory_hostname }}}}', '-e', f'restore_os_release_path={os_release_dir}/{{{{ inventory_hostname }}}}']
    if scenario != 'interactive-ha':
        args.extend(['-e', f'restore_selection={STAMP}', '-e', f'destructive_confirm=RESTORE_{CLUSTER}'])
    args.extend(['-e', 'restore_api_retries=1', '-e', 'restore_api_delay=0'])
    if scenario == 'bad-pair': (source / f'snapshot_{STAMP}.db').write_text('corrupt')
    if scenario == 'cpms-mismatch':
        manifest = source / f'.ocp-etcd-cronjob-managed-{STAMP}.json'
        data = yaml.safe_load(manifest.read_text()); data['cpms_enabled'] = True; manifest.write_text(__import__('json').dumps(data))
    if scenario == 'wrong-release': args[args.index('expected_ocp_version=4.20.3')] = 'expected_ocp_version=4.20.4'
    if scenario == 'rerun': state.mkdir(); (state / f'{STAMP}.json').write_text('{"phase":"FAILED"}')
    return fixture, trace, state, args


def run_interactive(args):
    """Run Ansible through a real controlling terminal and answer each pause once."""
    pid, fd = pty.fork()
    if pid == 0:
        os.tcsetpgrp(0, os.getpgrp())
        os.execvp(args[0], args)
    transcript = ''
    answers = (
        ('Enter one displayed backup timestamp', f'{STAMP}\r'),
        ('Type RESTORE_<cluster-id>', f'RESTORE_{CLUSTER}\r'),
    )
    answered = set()
    deadline = time.monotonic() + 90
    while True:
        if time.monotonic() > deadline:
            os.kill(pid, 9)
            os.waitpid(pid, 0)
            raise AssertionError(f'interactive restore timed out:\n{transcript}')
        ready, _, _ = select.select([fd], [], [], 1)
        if ready:
            try:
                data = os.read(fd, 4096)
            except OSError as exc:
                if exc.errno != errno.EIO:
                    raise
                data = b''
            transcript += data.decode(errors='replace')
            for prompt, answer in answers:
                if prompt in transcript and prompt not in answered:
                    os.write(fd, answer.encode())
                    answered.add(prompt)
        waited, status = os.waitpid(pid, os.WNOHANG)
        if waited:
            return subprocess.CompletedProcess(args, os.waitstatus_to_exitcode(status), transcript, '')


def run(scenario):
    fixture, trace, state, args = build(scenario)
    try:
        result = run_interactive(args) if scenario == 'interactive-ha' else subprocess.run(args, text=True, capture_output=True)
        out = result.stdout + result.stderr
        lines = trace.read_text().splitlines(); checkpoint = state / f'{STAMP}.json'
        if scenario in ('sno', 'ha', 'interactive-ha'):
            assert result.returncode == 0, out
            assert lines.count('restore') == 1 and not any(x.startswith('oc:') for x in lines[:lines.index('restore')]), lines
            assert lines.count('fence') == (0 if scenario == 'sno' else 3), lines
            assert 'VERIFIED' in checkpoint.read_text(), checkpoint.read_text()
            if scenario in ('ha', 'interactive-ha'): assert 'unsupportedConfigOverrides":null' in lines[-1], lines
            if scenario == 'interactive-ha':
                assert out.count('Enter one displayed backup timestamp') == 1, out
                assert out.count('Type RESTORE_<cluster-id>') == 1, out
        else:
            assert result.returncode != 0, out
            if scenario == 'api-fail':
                assert lines.count('restore') == 1 and any(x.startswith('oc:') for x in lines), lines
            else:
                assert 'restore' not in lines and not any(x.startswith('oc:') for x in lines), lines
            if scenario in ('fence-fail', 'api-fail'): assert 'FAILED' in checkpoint.read_text(), checkpoint.read_text()
        print(f'SIMULATED_ACTUAL_RESTORE_{scenario.upper().replace("-", "_")}_OK')
    finally:
        shutil.rmtree(fixture, ignore_errors=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('scenario', choices=['sno', 'ha', 'interactive-ha', 'bad-pair', 'wrong-release', 'identity-mismatch', 'release-mismatch', 'rhcos-mismatch', 'topology-mismatch', 'cpms-mismatch', 'fence-fail', 'api-fail', 'rerun'])
    run(parser.parse_args().scenario)
