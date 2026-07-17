"""Benchmark scenarios for the Sage coding harness.

Ten deterministic scenarios across four categories, exercised with a
ScriptedApiClient so no live LLM is required.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Scenario:
    """One benchmark scenario."""

    name: str
    category: str  # "read_explain", "controlled_edit", "policy_boundary", "memory_continuity"
    prompt: str
    workspace_files: dict[str, str] = field(default_factory=dict)
    # Scripted model responses (what the model "says" in order)
    model_responses: list[str] = field(default_factory=list)
    # Assertions
    expected_no_write: bool = False
    expected_files: dict[str, str] = field(default_factory=dict)  # path -> expected content
    expected_tool_calls: list[str] = field(default_factory=list)
    expected_denial: bool = False  # expect a policy denial
    expected_approval: bool = False  # expect an approval event
    memory_fact: str = ""  # for memory_continuity: fact to remember


SCENARIOS: list[Scenario] = [
    # --- Read and explain (3 scenarios) ---
    Scenario(
        name="read-readme",
        category="read_explain",
        prompt="读 README.md,告诉我项目名",
        workspace_files={"README.md": "# TourSwarm\nA travel planning agent."},
        model_responses=[
            '<tool>{"name":"read_file","args":{"path":"README.md"}}</tool>',
            "<final>项目名是 TourSwarm。</final>",
        ],
        expected_no_write=True,
        expected_tool_calls=["read_file"],
    ),
    Scenario(
        name="read-source-file",
        category="read_explain",
        prompt="读 src/app.py 解释它的功能",
        workspace_files={"src/app.py": "def main():\n    print('hello')\n"},
        model_responses=[
            '<tool>{"name":"read_file","args":{"path":"src/app.py"}}</tool>',
            "<final>这个文件定义了一个 main 函数,打印 hello。</final>",
        ],
        expected_no_write=True,
        expected_tool_calls=["read_file"],
    ),
    Scenario(
        name="trace-call-path",
        category="read_explain",
        prompt="从 main 到 output 的调用路径是什么",
        workspace_files={
            "main.py": "from handler import handle\nhandle()",
            "handler.py": "from output import print_result\nprint_result('done')",
            "output.py": "def print_result(msg): print(msg)",
        },
        model_responses=[
            '<tool>{"name":"read_file","args":{"path":"main.py"}}</tool>',
            '<tool>{"name":"read_file","args":{"path":"handler.py"}}</tool>',
            '<tool>{"name":"read_file","args":{"path":"output.py"}}</tool>',
            "<final>调用路径: main.py -> handler.handle -> output.print_result</final>",
        ],
        expected_no_write=True,
        expected_tool_calls=["read_file", "read_file", "read_file"],
    ),
    # --- Controlled edit (3 scenarios) ---
    Scenario(
        name="fix-typo",
        category="controlled_edit",
        prompt="修复 hello.py 里的 typo: prnit 改成 print",
        workspace_files={"hello.py": "def main():\n    prnit('hello')\n"},
        model_responses=[
            '<tool>{"name":"read_file","args":{"path":"hello.py"}}</tool>',
            '<tool>{"name":"patch_file","args":{"path":"hello.py","old_text":"prnit","new_text":"print"}}</tool>',
            "<final>已修复 typo。</final>",
        ],
        expected_files={"hello.py": "def main():\n    print('hello')\n"},
        expected_tool_calls=["read_file", "patch_file"],
    ),
    Scenario(
        name="add-test",
        category="controlled_edit",
        prompt="给 utils.py 的 add 函数加一个单元测试",
        workspace_files={"utils.py": "def add(a, b):\n    return a + b\n"},
        model_responses=[
            '<tool>{"name":"read_file","args":{"path":"utils.py"}}</tool>',
            '<tool>{"name":"write_file","args":{"path":"test_utils.py","content":"from utils import add\\ndef test_add():\\n    assert add(1, 2) == 3\\n"}}</tool>',
            "<final>已添加测试。</final>",
        ],
        expected_files={
            "test_utils.py": "from utils import add\ndef test_add():\n    assert add(1, 2) == 3\n"
        },
        expected_tool_calls=["read_file", "write_file"],
    ),
    Scenario(
        name="add-function",
        category="controlled_edit",
        prompt="在 utils.py 里加一个 subtract 函数",
        workspace_files={"utils.py": "def add(a, b):\n    return a + b\n"},
        model_responses=[
            '<tool>{"name":"read_file","args":{"path":"utils.py"}}</tool>',
            '<tool>{"name":"patch_file","args":{"path":"utils.py","old_text":"    return a + b","new_text":"    return a + b\\n\\ndef subtract(a, b):\\n    return a - b"}}</tool>',
            "<final>已添加 subtract 函数。</final>",
        ],
        expected_files={
            "utils.py": "def add(a, b):\n    return a + b\n\ndef subtract(a, b):\n    return a - b\n"
        },
        expected_tool_calls=["read_file", "patch_file"],
    ),
    # --- Policy boundary (2 scenarios) ---
    Scenario(
        name="plan-mode-blocks-write",
        category="policy_boundary",
        prompt="在 plan 模式下写一个文件",
        workspace_files={},
        model_responses=[
            '<tool>{"name":"write_file","args":{"path":"test.txt","content":"hello"}}</tool>',
            "<final>无法在计划模式下写入文件。</final>",
        ],
        expected_denial=True,
        expected_no_write=True,
    ),
    Scenario(
        name="default-mode-requires-approval",
        category="policy_boundary",
        prompt="写一个文件",
        workspace_files={},
        model_responses=[
            '<tool>{"name":"write_file","args":{"path":"test.txt","content":"hello"}}</tool>',
            "<final>已写入文件。</final>",
        ],
        expected_approval=True,
    ),
    # --- Memory continuity (2 scenarios) ---
    Scenario(
        name="remember-test-command",
        category="memory_continuity",
        prompt="/remember 这个项目用 pytest 跑测试",
        workspace_files={},
        model_responses=[
            '<tool>{"name":"tool_search","args":{"query":"remember"}}</tool>',
            '<tool>{"name":"remember","args":{"fact":"这个项目用 pytest 跑测试"}}</tool>',
            "<final>已记住:这个项目用 pytest 跑测试。</final>",
        ],
        memory_fact="这个项目用 pytest 跑测试",
        expected_tool_calls=["tool_search", "remember"],
        expected_approval=True,
    ),
    Scenario(
        name="remember-convention",
        category="memory_continuity",
        prompt="/remember 代码风格用 4 空格缩进",
        workspace_files={},
        model_responses=[
            '<tool>{"name":"tool_search","args":{"query":"remember"}}</tool>',
            '<tool>{"name":"remember","args":{"fact":"代码风格用 4 空格缩进"}}</tool>',
            "<final>已记住:代码风格用 4 空格缩进。</final>",
        ],
        memory_fact="代码风格用 4 空格缩进",
        expected_tool_calls=["tool_search", "remember"],
        expected_approval=True,
    ),
]
