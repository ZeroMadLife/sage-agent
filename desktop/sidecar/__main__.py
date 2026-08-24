"""Process entry point for the minimal desktop sidecar."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sys
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from desktop.sidecar.app import DESKTOP_API_VERSION, DESKTOP_PROFILE, create_desktop_app


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sage-api-aarch64-apple-darwin")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--data-dir", type=Path, required=True)
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


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.bind != "127.0.0.1":
        _parser().error("--bind must be 127.0.0.1")
    if args.port != 0:
        _parser().error("--port must be 0 so the OS assigns the loopback port")

    build_sha = args.build_sha or _embedded_build_sha()
    app = create_desktop_app(data_dir=args.data_dir, build_sha=build_sha)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((args.bind, 0))
    listener.listen(socket.SOMAXCONN)
    port = int(listener.getsockname()[1])
    receipt = {
        "event": "sidecar_started",
        "pid": os.getpid(),
        "bind": args.bind,
        "port": port,
        "profile": DESKTOP_PROFILE,
        "api_version": DESKTOP_API_VERSION,
        "build_sha": build_sha,
    }
    print(json.dumps(receipt, sort_keys=True), flush=True)

    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
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
