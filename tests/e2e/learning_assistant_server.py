"""Supervise the Learning E2E uvicorn process across one intentional restart."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

_RESTART_EXIT_CODE = 75
_RUNTIME_ROOT = Path("output/playwright/learning-e2e-runtime").resolve()
_child: subprocess.Popen[bytes] | None = None


def _forward_signal(signum: int, frame: object) -> None:
    del frame
    if _child is not None and _child.poll() is None:
        _child.send_signal(signum)


def main() -> int:
    global _child
    shutil.rmtree(_RUNTIME_ROOT, ignore_errors=True)
    _RUNTIME_ROOT.mkdir(parents=True)
    environment = dict(os.environ)
    environment["SAGE_E2E_RUNTIME_ROOT"] = str(_RUNTIME_ROOT)
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, _forward_signal)
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "tests.e2e.learning_assistant_app:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8765",
    ]
    while True:
        _child = subprocess.Popen(command, env=environment)
        return_code = _child.wait()
        if return_code != _RESTART_EXIT_CODE:
            return return_code


if __name__ == "__main__":
    raise SystemExit(main())
