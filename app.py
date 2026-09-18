"""A stateful test app: one marker file on the persistent data disk.

The block-store suite needs an app whose ENTIRE observable state is a single
value it wrote to `/dev/vdb`, so that "the disk came back" and "the app came
back" are different questions with different answers. Everything here exists to
keep them apart:

* `/health` reports whether `data_dir` is a MOUNT POINT and which device backs
  it. Without that, an app whose data disk never attached still writes its
  marker — to the rootfs — and reads it back happily for as long as that VM
  lives. The test asserts `mounted` before it trusts a single later read.
* the marker is written with an explicit `fsync` of the file AND its directory.
  A backup is a copy of the BLOCK DEVICE taken while the guest is paused; a
  value still sitting in the guest's page cache is not on the device, and a
  restore would then honestly report the absence of a value the test believes
  it wrote.
* a read reports the value verbatim. The test writes a per-run random value, so
  a marker left by an earlier run cannot be mistaken for this run's.

Stdlib only, no build step: the image is `python:3.12-alpine` + this file.
"""

from __future__ import annotations

import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "3000"))
DATA_DIR = os.environ.get("DATA_DIR", "/data")
MARKER = os.path.join(DATA_DIR, "marker")
MAX_BODY = 64 * 1024


def _mount_device(path: str) -> str | None:
    """The device backing `path`, read from the kernel rather than inferred.

    `os.path.ismount` answers whether something is mounted there; it does not
    say what. A test that has to prove the guest is writing to `/dev/vdb` and
    not to the rootfs needs the name.
    """
    try:
        target = os.path.realpath(path)
        with open("/proc/self/mountinfo", encoding="utf-8") as handle:
            for line in handle:
                fields = line.split(" - ")
                if len(fields) < 2:
                    continue
                mount_point = fields[0].split()[4]
                source = fields[1].split()[1]
                if mount_point == target:
                    return source
    except OSError:
        return None
    return None


def _write_marker(value: str) -> None:
    """Write the marker and put it on the DEVICE, not in the page cache."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(MARKER, "w", encoding="utf-8") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    # The file's name lives in the directory, which has its own dirty pages.
    directory = os.open(DATA_DIR, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    subprocess.run(["sync"], check=False)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _reply(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._reply(200, {"app": "stateful-marker-app", "ready": True})
        elif path == "/health":
            self._reply(
                200,
                {
                    "ok": True,
                    "data_dir": DATA_DIR,
                    "mounted": os.path.ismount(DATA_DIR),
                    "device": _mount_device(DATA_DIR),
                },
            )
        elif path == "/marker":
            try:
                with open(MARKER, encoding="utf-8") as handle:
                    self._reply(200, {"marker": handle.read()})
            except FileNotFoundError:
                self._reply(404, {"error": "no marker", "path": MARKER})
            except OSError as error:
                self._reply(500, {"error": str(error), "path": MARKER})
        else:
            self._reply(404, {"error": "no such route", "path": path})

    def do_PUT(self) -> None:
        if self.path.split("?", 1)[0] != "/marker":
            self._reply(404, {"error": "no such route", "path": self.path})
            return
        length = int(self.headers.get("content-length") or 0)
        if length > MAX_BODY:
            self._reply(413, {"error": "marker too large", "limit": MAX_BODY})
            return
        value = self.rfile.read(length).decode("utf-8")
        try:
            _write_marker(value)
        except OSError as error:
            # A write that cannot reach the disk must be LOUD: this is the app
            # reporting that its data disk is not usable, which is a real
            # platform failure and never a reason to answer 200.
            self._reply(500, {"error": str(error), "path": MARKER})
            return
        self._reply(201, {"marker": value, "path": MARKER})

    def do_POST(self) -> None:
        self.do_PUT()

    def log_message(self, fmt: str, *args) -> None:
        print(f"[marker-app] {fmt % args}", flush=True)


def main() -> None:
    print(f"[marker-app] listening on 0.0.0.0:{PORT}, data_dir={DATA_DIR}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
