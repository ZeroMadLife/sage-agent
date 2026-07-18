"""Operator-only commands for Sage cloud authentication."""

from __future__ import annotations

import argparse
import asyncio
import secrets
from collections.abc import Sequence

from core.cloud.auth.repository import CloudRepository
from db.database import AsyncSessionFactory


async def create_one_time_invite(email: str = "") -> str:
    """Create one high-entropy invite and return its one-time plaintext value."""
    code = secrets.token_urlsafe(24)
    repository = CloudRepository(AsyncSessionFactory)
    await repository.create_invite(code, email=email)
    return code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sage 云认证运维命令")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create-invite", help="创建一次性登录邀请码")
    create.add_argument(
        "--email",
        default="",
        help="可选：仅允许匹配此 GitHub 主邮箱的用户消费",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "create-invite":
        raise RuntimeError("unsupported command")
    code = asyncio.run(create_one_time_invite(args.email.strip()))
    print(f"一次性邀请码（仅显示一次）: {code}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
