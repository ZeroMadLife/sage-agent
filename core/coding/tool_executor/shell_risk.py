"""集中识别已知危险 Shell 命令，供 Policy 与 Approval 复用。"""

from __future__ import annotations

import re

DANGEROUS_PATTERNS: tuple[tuple[str, str, str], ...] = (
    (
        r"\brm\b[^\n;|&]*(?:\s-[^-\s;|&]*r[^\s;|&]*|\s--recursive)(?:\s|$)",
        "Recursive delete command requires approval.",
        "rm_recursive",
    ),
    (r"\bgit\s+reset\s+--hard\b", "Hard git reset can discard work.", "git_reset_hard"),
    (
        r"\bgit\s+push\b[^\n;|&]*--force",
        "Force push can overwrite remote history.",
        "git_force_push",
    ),
    (r"\bchmod\s+777\b", "World-writable permission change requires approval.", "chmod_777"),
    (
        r"\bcurl\b.*\|\s*(sh|bash)\b",
        "Piping remote curl output to shell requires approval.",
        "curl_pipe_shell",
    ),
    (
        r"\bwget\b.*\|\s*(sh|bash)\b",
        "Piping remote wget output to shell requires approval.",
        "wget_pipe_shell",
    ),
    (r"(^|\s)sudo(\s|$)", "sudo command requires approval.", "sudo"),
    (r"(^|\s)>+\s*/etc/", "Writing into /etc requires approval.", "write_etc"),
    (r"(^|\s)>+\s*~/.ssh/", "Writing into ~/.ssh requires approval.", "write_ssh"),
    (
        r"\bdocker\s+compose\s+down\b",
        "Stopping compose services requires approval.",
        "docker_compose_down",
    ),
    (r"\bkill\s+-9\b", "Force-killing processes requires approval.", "kill_9"),
)


def check_dangerous_command(command: str) -> tuple[bool, str, str]:
    """返回危险标记、审批说明和稳定规则键，不在这里执行授权决策。"""
    for pattern, description, pattern_key in DANGEROUS_PATTERNS:
        if re.search(pattern, command, flags=re.IGNORECASE | re.DOTALL):
            return True, description, pattern_key
    return False, "", ""
