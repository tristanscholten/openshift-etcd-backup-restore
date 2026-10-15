#!/usr/bin/env python3
"""Delete one Machine through an authenticated oc proxy with a UID precondition."""
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def fail(message: str) -> None:
    raise SystemExit(f"UID_PRECONDITION_DELETE_FAILED: {message}")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def main() -> None:
    if len(sys.argv) != 6:
        raise SystemExit("usage: delete_machine_with_uid_precondition.py OC KUBECONFIG NAMESPACE NAME UID")
    oc, kubeconfig, namespace, name, uid = sys.argv[1:]
    if not all((namespace, name, uid)):
        fail("Machine namespace, name, and reconciled UID are required")

    port = free_port()
    proxy = subprocess.Popen(
        [oc, f"--kubeconfig={kubeconfig}", "proxy", "--address=127.0.0.1", "--accept-hosts=^127\\.0\\.0\\.1$", f"--port={port}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        endpoint = f"http://127.0.0.1:{port}/apis/machine.openshift.io/v1beta1/namespaces/{urllib.parse.quote(namespace, safe='')}/machines/{urllib.parse.quote(name, safe='')}"
        body = json.dumps({
            "apiVersion": "v1",
            "kind": "DeleteOptions",
            "preconditions": {"uid": uid},
        }, separators=(",", ":")).encode()
        request = urllib.request.Request(endpoint, data=body, method="DELETE", headers={"Content-Type": "application/json"})
        deadline = time.monotonic() + 15
        while True:
            try:
                with urllib.request.urlopen(request, timeout=3) as response:
                    if response.status not in (200, 202):
                        fail(f"unexpected API status {response.status}")
                return
            except urllib.error.HTTPError as error:
                fail(f"API DELETE refused ({error.code})")
            except urllib.error.URLError as error:
                if time.monotonic() >= deadline:
                    fail(f"authenticated oc proxy did not become ready: {error.reason}")
                time.sleep(0.1)
    finally:
        proxy.terminate()
        try:
            proxy.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proxy.kill()
            proxy.wait()


if __name__ == "__main__":
    main()
