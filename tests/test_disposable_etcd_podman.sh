#!/usr/bin/env bash
# SIMULATED disposable etcd mechanics test. This is not OpenShift DR proof.
set -euo pipefail

image=${ETCD_TEST_IMAGE:-quay.io/coreos/etcd:v3.5.15}
name="ocp-etcd-fixture-$$"
work=$(mktemp -d)
cleanup() {
  podman rm -f "$name" >/dev/null 2>&1 || true
  rm -rf -- "$work"
}
trap cleanup EXIT

podman run -d --name "$name" -v "$work":/snap:Z "$image" \
  etcd --name node0 --data-dir /var/lib/etcd \
  --listen-client-urls http://0.0.0.0:2379 \
  --advertise-client-urls http://0.0.0.0:2379 >/dev/null
for _ in $(seq 1 30); do
  if podman exec "$name" etcdctl --endpoints=http://127.0.0.1:2379 endpoint health; then break; fi
  sleep 1
done
podman exec "$name" etcdctl --endpoints=http://127.0.0.1:2379 put fixture-marker restored-value
podman exec "$name" etcdctl --endpoints=http://127.0.0.1:2379 snapshot save /snap/fixture.db
podman exec "$name" etcdutl snapshot status /snap/fixture.db --write-out=json
podman rm -f "$name" >/dev/null
podman run --rm -v "$work":/snap:Z "$image" etcdutl snapshot restore /snap/fixture.db \
  --data-dir /snap/restored --name node0 \
  --initial-cluster node0=http://127.0.0.1:2380 \
  --initial-advertise-peer-urls http://127.0.0.1:2380
podman run -d --name "$name" -v "$work":/snap:Z "$image" \
  etcd --name node0 --data-dir /snap/restored \
  --listen-client-urls http://0.0.0.0:2379 \
  --advertise-client-urls http://0.0.0.0:2379 \
  --listen-peer-urls http://0.0.0.0:2380 \
  --initial-advertise-peer-urls http://0.0.0.0:2380 \
  --initial-cluster node0=http://0.0.0.0:2380 >/dev/null
for _ in $(seq 1 30); do
  if value=$(podman exec "$name" etcdctl --endpoints=http://127.0.0.1:2379 get fixture-marker --print-value-only); then
    [[ $value == restored-value ]]
    printf 'SIMULATED_ETCD_MARKER_RESTORED=%s\n' "$value"
    exit 0
  fi
  sleep 1
done
printf 'fixture marker did not become readable after restore\n' >&2
exit 1
