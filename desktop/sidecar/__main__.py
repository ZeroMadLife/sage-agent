"""Process entry point for the minimal desktop sidecar."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sys
import threading
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from desktop.sidecar.app import DESKTOP_API_VERSION, DESKTOP_PROFILE, create_desktop_app
from desktop.sidecar.security import DesktopBootstrap, DesktopSecurity


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sage-api-aarch64-apple-darwin")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--desktop-host", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--build-sha", default=None, help=argparse.SUPPRESS)
    return parser


def _embedded_build_sha() -> str:
    if not getattr(sys, "frozen", False):
        return "dev"
    receipt_path = Path(sys.executable).resolve().parent / "build-receipt.json"
    try:
        payload = json.loads(receipt_path.read_text(encoding="utf-8"))
        source_sha = payload["source_sha"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        return "unknown"
    return str(source_sha)


def _monitor_parent_pipe(server: uvicorn.Server) -> None:
    """Stop the secure sidecar when the desktop host closes its inherited pipe."""

    try:
        while sys.stdin.buffer.read(4096):
            pass
    except OSError:
        pass
    server.should_exit = True


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.bind != "127.0.0.1":
        parser.error("--bind must be 127.0.0.1")
    if args.port != 0:
        parser.error("--port must be 0 so the OS assigns the loopback port")

    bootstrap: DesktopBootstrap | None = None
    if args.desktop_host:
        raw = sys.stdin.buffer.readline(64 * 1024 + 1)
        try:
            bootstrap = DesktopBootstrap.from_json(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            print("desktop bootstrap rejected", file=sys.stderr)
            return 2
        data_dir = bootstrap.data_dir
    else:
        if args.data_dir is None:
            parser.error("--data-dir is required unless --desktop-host is used")
        data_dir = args.data_dir

    build_sha = args.build_sha or _embedded_build_sha()
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((args.bind, 0))
    listener.listen(socket.SOMAXCONN)
    port = int(listener.getsockname()[1])
    security = (
        DesktopSecurity(
            bearer=bootstrap.bearer,
            origin=bootstrap.origin,
            host=f"127.0.0.1:{port}",
        )
        if bootstrap is not None
        else None
    )
    app = create_desktop_app(
        data_dir=data_dir,
        build_sha=build_sha,
        security=security,
        runtime=bootstrap.runtime if bootstrap is not None else None,
    )
    if bootstrap is None:
        receipt = {
            "event": "sidecar_started",
            "pid": os.getpid(),
            "bind": args.bind,
            "port": port,
            "profile": DESKTOP_PROFILE,
            "api_version": DESKTOP_API_VERSION,
            "build_sha": build_sha,
        }
    else:
        receipt = {
            "pid": os.getpid(),
            "port": port,
            "instance_id": bootstrap.instance_id,
            "api_version": DESKTOP_API_VERSION,
            "build_sha": build_sha,
            "nonce": bootstrap.nonce,
        }
    print(json.dumps(receipt, sort_keys=True), flush=True)

    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
    if bootstrap is not None:
        threading.Thread(
            target=_monitor_parent_pipe,
            args=(server,),
            name="sage-desktop-parent-pipe",
            daemon=True,
        ).start()
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def request_shutdown(_: int, __: object) -> None:
        server.should_exit = True

    signal.signal(signal.SIGTERM, request_shutdown)
    try:
        server.run(sockets=[listener])
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        listener.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
