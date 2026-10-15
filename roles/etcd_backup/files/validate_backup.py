#!/usr/bin/env python3
"""Read-only validation of one managed backup set; prints metadata only."""
import hashlib, json, os, pathlib, sys

def fail(message):
    raise SystemExit(message)

def valid_set(root, set_id):
    if not set_id.startswith('set-') or '/' in set_id or '..' in set_id:
        fail('unsafe set id')
    root = pathlib.Path(root).resolve(strict=True)
    candidate = root / set_id
    if candidate.parent != root or candidate.is_symlink() or not candidate.is_dir():
        fail('invalid set directory')
    complete = candidate / 'COMPLETE'
    metadata = candidate / 'metadata.json'
    sums = candidate / 'SHA256SUMS'
    if not complete.is_file() or not metadata.is_file() or not sums.is_file():
        fail('incomplete set')
    data = json.loads(metadata.read_text())
    required = {'schema', 'set_id', 'ocp_version', 'cluster_id', 'snapshot', 'static_resources'}
    if not required <= data.keys() or data['set_id'] != set_id or data['schema'] != 1:
        fail('invalid metadata')
    if not data['ocp_version'].startswith('4.20.'):
        fail('unsupported backup version')
    entries = {}
    for line in sums.read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        entries[name] = digest
    for name in (data['snapshot'], data['static_resources']):
        path = candidate / name
        if path.parent != candidate or path.is_symlink() or not path.is_file():
            fail('invalid backup member')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if entries.get(name) != digest:
            fail('hash mismatch')
    return data

if __name__ == '__main__':
    print(json.dumps(valid_set(sys.argv[1], sys.argv[2]), sort_keys=True))
