"""Operator-only commands for Sage cloud authentication."""

from __future__ import annotations

import argparse
import asyncio
import secrets
from collections.abc import Sequence

from core.cloud.auth.models import CloudLoginSession
from core.cloud.auth.repository import CloudRepository
from db.database import AsyncSessionFactory


async def create_one_time_invite(email: str = "") -> str:
    """Create one high-entropy invite and return its one-time plaintext value."""
    code = secrets.token_urlsafe(24)
    repository = CloudRepository(AsyncSessionFactory)
    await repository.create_invite(code, email=email)
    return code


async def list_devices(email: str) -> list[CloudLoginSession]:
    """Return active device metadata without exposing browser tokens."""
    return await CloudRepository(AsyncSessionFactory).list_active_sessions(email)


async def revoke_device(email: str, session_id: str) -> bool:
    """Revoke one device session for an account."""
    return await CloudRepository(AsyncSessionFactory).revoke_device_session(email, session_id)


async def disable_account(email: str) -> bool:
    """Disable an account and revoke all of its active device sessions."""
    return await CloudRepository(AsyncSessionFactory).disable_user(email)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sage 云认证运维命令")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create-invite", help="创建一次性登录邀请码")
    create.add_argument(
        "--email",
        default="",
        help="可选：仅允许此账号邮箱消费；私有 Canary 直接登录必须填写",
    )
    devices = subparsers.add_parser("list-devices", help="查看账号的活动设备")
    devices.add_argument("--email", required=True, help="账号邮箱")
    revoke = subparsers.add_parser("revoke-device", help="撤销一个设备登录")
    revoke.add_argument("--email", required=True, help="账号邮箱")
    revoke.add_argument("--session-id", required=True, help="设备会话 ID")
    disable = subparsers.add_parser("disable-account", help="停用账号并撤销全部设备")
    disable.add_argument("--email", required=True, help="账号邮箱")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "create-invite":
        code = asyncio.run(create_one_time_invite(args.email.strip()))
        print(f"一次性邀请码（仅显示一次）: {code}")
    elif args.command == "list-devices":
        sessions = asyncio.run(list_devices(args.email.strip()))
        if not sessions:
            print("没有活动设备")
        for session in sessions:
            print(
                f"{session.session_id} | {session.device_name} | "
                f"过期 {session.expires_at.isoformat()} | 最近活动 "
                f"{session.last_seen_at.isoformat() if session.last_seen_at else '-'}"
            )
    elif args.command == "revoke-device":
        revoked = asyncio.run(revoke_device(args.email.strip(), args.session_id.strip()))
        print("已撤销设备" if revoked else "未找到活动设备")
    elif args.command == "disable-account":
        disabled = asyncio.run(disable_account(args.email.strip()))
        print("账号已停用" if disabled else "未找到账号")
    else:
        raise RuntimeError("unsupported command")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
