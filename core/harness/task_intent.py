"""在 Plan 之前生成不含正文的确定性任务意图信封。"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, cast

IntentKind = Literal["answer", "research", "code_change", "review", "teach", "skill", "general"]
TaskShape = Literal["direct", "sequential", "parallel_candidate"]
RequestedEffect = Literal["read", "write", "execute", "external"]
CapabilityHint = Literal["files", "shell", "knowledge", "memory", "web", "mcp", "skill"]
RiskHint = Literal["destructive", "privileged", "network", "external_side_effect", "credential"]
ConstraintCode = Literal["read_only", "no_tools", "no_web", "no_external", "require_approval"]

_INTENT_KINDS = frozenset(
    {"answer", "research", "code_change", "review", "teach", "skill", "general"}
)
_TASK_SHAPES = frozenset({"direct", "sequential", "parallel_candidate"})
_EFFECTS = frozenset({"read", "write", "execute", "external"})
_CAPABILITIES = frozenset({"files", "shell", "knowledge", "memory", "web", "mcp", "skill"})
_RISKS = frozenset({"destructive", "privileged", "network", "external_side_effect", "credential"})
_CONSTRAINTS = frozenset({"read_only", "no_tools", "no_web", "no_external", "require_approval"})

_SLASH_COMMAND = re.compile(r"^\s*/(?P<name>[A-Za-z0-9_-]+)(?:\s|$)")
_REVIEW = re.compile(
    r"(?:code\s*review|review|审查|审核|评审|检查.*(?:代码|仓库|diff)|看看有没有问题)", re.I
)
_RESEARCH = re.compile(r"(?:调研|研究|对比|竞品|最新|近期|官网|官方资料|搜索|检索|资料)", re.I)
_TEACH = re.compile(r"(?:教学|教我|讲解|解释|怎么理解|带我.*学|原理是什么|为什么这样)", re.I)
_ANSWER = re.compile(r"(?:\?|？|等于多少|是多少|是否|what\b|why\b|how\b|等于)", re.I)
_CHANGE = re.compile(
    r"(?:修复|改一下|修改|实现|开发|重构|新增|删除|移除|补上|接入|落地|fix|implement|refactor|add|remove)",
    re.I,
)
_EXECUTE = re.compile(
    r"(?:运行|执行|测试|构建|编译|启动|部署|shell|bash|命令|command|pytest|npm\s+test|git\s+)",
    re.I,
)
_EXTERNAL = re.compile(r"(?:发布|推送|push|deploy|merge|合并.*(?:分支|PR)|发送|发消息|安装)", re.I)
_DANGEROUS = re.compile(
    r"(?:rm\s+-[a-z]*r|git\s+reset\s+--hard|drop\s+table|truncate|format\s+)", re.I
)
_PRIVILEGED = re.compile(r"(?:^|\s)(?:sudo|root|chmod\s+7|chown\s)", re.I)
_CREDENTIAL = re.compile(r"(?:api[_ -]?key|access[_ -]?token|password|secret|密钥|令牌|密码)", re.I)
_NO_TOOLS = re.compile(r"(?:不要|无需|禁止|不准)\s*(?:调用|使用|执行)\s*(?:任何)?\s*工具", re.I)
_NO_WEB = re.compile(
    r"(?:不要|无需|禁止|不准|不)\s*(?:联网|上网|访问网络|使用网络|联网搜索|调用网络)", re.I
)
_READ_ONLY = re.compile(r"(?:只读|不要修改|别修改|不修改|不要写|不写文件|不改文件|仅审查)", re.I)
_NO_EXTERNAL = re.compile(
    r"(?:不要发布|不要推送|不要合并|不要发送|不要部署|不执行外部副作用)", re.I
)
_APPROVAL = re.compile(r"(?:需要审批|先审批|先问我|询问我|等待确认)", re.I)
_PARALLEL = re.compile(r"(?:并行|同时|分别|in\s+parallel|parallel)", re.I)
_SEQUENTIAL = re.compile(r"(?:先.+再|然后|之后|接着|第一步|第二步|最后|依次)", re.I)
_FILES = re.compile(
    r"(?:文件|目录|路径|仓库|源码|代码|README|diff|commit|worktree|project|repo)", re.I
)
_SHELL = re.compile(r"(?:shell|bash|终端|命令行|命令|pytest|npm\s+test|git\s+|rm\s+-)", re.I)
_MEMORY = re.compile(r"(?:记忆|长期记忆|个人偏好|我的习惯|之前告诉过你|上次记住)", re.I)
_WEB = re.compile(r"(?:web|联网|网页|官网|官方资料|互联网|网络|最新|近期|当前版本)", re.I)
_MCP = re.compile(r"(?:\bmcp\b|远程工具|外部工具)", re.I)
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass(frozen=True, slots=True)
class TaskIntentEnvelope:
    """Plan admission 使用的固定结构；不允许携带用户正文或模型推理。"""

    intent_kind: IntentKind = "general"
    task_shape: TaskShape = "direct"
    requested_effects: tuple[RequestedEffect, ...] = ("read",)
    capability_hints: tuple[CapabilityHint, ...] = ()
    risk_hints: tuple[RiskHint, ...] = ()
    explicit_constraints: tuple[ConstraintCode, ...] = ()
    classifier_version: str = "deterministic-v1"

    def __post_init__(self) -> None:
        """校验枚举、排序和有界版本，避免意图信封成为自由文本容器。"""
        if self.intent_kind not in _INTENT_KINDS:
            raise ValueError(f"unsupported intent kind: {self.intent_kind}")
        if self.task_shape not in _TASK_SHAPES:
            raise ValueError(f"unsupported task shape: {self.task_shape}")
        for field_name, values, allowed in (
            ("requested effects", self.requested_effects, _EFFECTS),
            ("capability hints", self.capability_hints, _CAPABILITIES),
            ("risk hints", self.risk_hints, _RISKS),
            ("explicit constraints", self.explicit_constraints, _CONSTRAINTS),
        ):
            if tuple(sorted(set(values))) != values:
                raise ValueError(f"{field_name} must be sorted and unique")
            if any(item not in allowed for item in values):
                raise ValueError(f"unsupported value in {field_name}")
        if (
            not isinstance(self.classifier_version, str)
            or _VERSION.fullmatch(self.classifier_version) is None
        ):
            raise ValueError("classifier_version must be bounded")

    @property
    def can_only_narrow_capabilities(self) -> bool:
        """这是数据契约不变量：意图永远不能授予 Permission 或执行权限。"""
        return True

    def as_dict(self) -> dict[str, object]:
        """返回可持久化的脱敏投影，不包含用户输入、Skill 正文或 CoT。"""
        return {
            "intent_kind": self.intent_kind,
            "task_shape": self.task_shape,
            "requested_effects": list(self.requested_effects),
            "capability_hints": list(self.capability_hints),
            "risk_hints": list(self.risk_hints),
            "explicit_constraints": list(self.explicit_constraints),
            "classifier_version": self.classifier_version,
            "authority": "narrow_only",
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> TaskIntentEnvelope:
        """从已验证的 Plan admission 恢复信封，不重新读取用户输入。"""
        if value.get("authority") != "narrow_only":
            raise ValueError("task intent authority must be narrow_only")

        def sequence(name: str) -> tuple[str, ...]:
            raw = value.get(name, ())
            if not isinstance(raw, Sequence) or isinstance(raw, str | bytes | bytearray):
                raise ValueError(f"task intent {name} must be a sequence")
            return tuple(str(item) for item in raw)

        return cls(
            intent_kind=cast(IntentKind, str(value.get("intent_kind", "general"))),
            task_shape=cast(TaskShape, str(value.get("task_shape", "direct"))),
            requested_effects=cast(tuple[RequestedEffect, ...], sequence("requested_effects")),
            capability_hints=cast(tuple[CapabilityHint, ...], sequence("capability_hints")),
            risk_hints=cast(tuple[RiskHint, ...], sequence("risk_hints")),
            explicit_constraints=cast(tuple[ConstraintCode, ...], sequence("explicit_constraints")),
            classifier_version=str(value.get("classifier_version", "")),
        )

    def narrow_retrieval_sources(self, candidates: tuple[str, ...]) -> tuple[str, ...]:
        """按能力提示收窄 Retrieval 候选；unknown/general 保留旧路由。"""
        if self.intent_kind == "general":
            return candidates
        if "no_web" in self.explicit_constraints:
            candidates = tuple(source for source in candidates if source != "web")
        source_hints = {
            "semantic_memory": "memory",
            "episodic_memory": "memory",
            "knowledge": "knowledge",
            "web": "web",
        }
        allowed = {hint for hint in self.capability_hints if hint in {"memory", "knowledge", "web"}}
        if not allowed:
            # 没有明确来源 hint 时保持旧 Retrieval 路由，避免把“检索/研究”
            # 误判成禁止 Knowledge、MCP 或其他既有能力。
            return candidates
        return tuple(source for source in candidates if source_hints.get(source) in allowed)

    def allows_tool_candidate(self, *, origin: str, category: str, tool_name: str = "") -> bool:
        """只在候选选择阶段做收窄；最终 Permission/Policy 仍由执行层裁决。"""
        if self.intent_kind == "general":
            return True
        if "no_tools" in self.explicit_constraints:
            return False
        if "no_external" in self.explicit_constraints and origin in {"web", "mcp"}:
            return False
        has_capability_hints = bool(self.capability_hints)
        if origin == "web":
            return "web" in self.capability_hints or not has_capability_hints
        if origin == "mcp":
            return "mcp" in self.capability_hints or not has_capability_hints
        if origin == "skill":
            return "skill" in self.capability_hints or not has_capability_hints
        if origin == "subagent":
            return self.intent_kind in {"research", "code_change"}
        if category == "file":
            if "read_only" in self.explicit_constraints and tool_name in {
                "write_file",
                "patch_file",
            }:
                return False
            return "files" in self.capability_hints or not has_capability_hints
        if category == "shell":
            return "read_only" not in self.explicit_constraints and (
                "shell" in self.capability_hints or not has_capability_hints
            )
        if category in {"knowledge", "memory"}:
            return category in self.capability_hints or not has_capability_hints
        if category == "agent":
            return self.intent_kind in {"research", "code_change"}
        return True


class TaskIntentAnalyzer:
    """用规则从一条输入生成确定性、无正文的 TaskIntentEnvelope。"""

    def __init__(self, *, classifier_version: str = "deterministic-v1") -> None:
        """固定分类器版本，便于 Plan/Resume 审计和未来升级。"""
        self._classifier_version = classifier_version

    def analyze(self, user_message: str) -> TaskIntentEnvelope:
        """分析一次新 Turn；正文只在当前调用使用，不写入返回对象。"""
        normalized = " ".join(str(user_message).split())[:8_000]
        slash = bool(_SLASH_COMMAND.match(normalized))
        intent_kind: IntentKind
        if slash:
            intent_kind = "skill"
        elif _REVIEW.search(normalized):
            intent_kind = "review"
        elif _CHANGE.search(normalized):
            intent_kind = "code_change"
        elif _RESEARCH.search(normalized):
            intent_kind = "research"
        elif _TEACH.search(normalized):
            intent_kind = "teach"
        elif _ANSWER.search(normalized):
            intent_kind = "answer"
        else:
            intent_kind = "general"

        shape: TaskShape = (
            "parallel_candidate"
            if _PARALLEL.search(normalized)
            else "sequential"
            if _SEQUENTIAL.search(normalized)
            else "direct"
        )
        constraints: set[ConstraintCode] = set()
        if _READ_ONLY.search(normalized) or intent_kind == "review":
            constraints.add("read_only")
        if _NO_TOOLS.search(normalized):
            constraints.add("no_tools")
        if _NO_WEB.search(normalized):
            constraints.add("no_web")
        if _NO_EXTERNAL.search(normalized):
            constraints.add("no_external")
        if _APPROVAL.search(normalized):
            constraints.add("require_approval")

        effects: set[RequestedEffect] = {"read"}
        if intent_kind == "code_change" and "read_only" not in constraints:
            effects.add("write")
        if _EXECUTE.search(normalized) or _DANGEROUS.search(normalized):
            effects.add("execute")
        if _EXTERNAL.search(normalized):
            effects.update({"execute", "external"})

        hints: set[CapabilityHint] = set()
        if _FILES.search(normalized) or intent_kind in {"code_change", "review"}:
            hints.add("files")
        if _SHELL.search(normalized) or "execute" in effects:
            hints.add("shell")
        if _MEMORY.search(normalized):
            hints.add("memory")
        if _RESEARCH.search(normalized) and re.search(
            r"(?:知识库|文档库|资料|wiki|源码)", normalized, re.I
        ):
            hints.add("knowledge")
        if _WEB.search(normalized) and "no_web" not in constraints:
            hints.add("web")
        if _MCP.search(normalized):
            hints.add("mcp")
        if slash:
            hints.add("skill")
            if "/" in normalized or "\\" in normalized:
                hints.add("files")

        risks: set[RiskHint] = set()
        if _DANGEROUS.search(normalized):
            risks.add("destructive")
        if _PRIVILEGED.search(normalized):
            risks.add("privileged")
        if "web" in hints or "mcp" in hints:
            risks.add("network")
        if "external" in effects:
            risks.add("external_side_effect")
        if _CREDENTIAL.search(normalized):
            risks.add("credential")

        return TaskIntentEnvelope(
            intent_kind=intent_kind,
            task_shape=shape,
            requested_effects=tuple(sorted(effects)),
            capability_hints=tuple(sorted(hints)),
            risk_hints=tuple(sorted(risks)),
            explicit_constraints=tuple(sorted(constraints)),
            classifier_version=self._classifier_version,
        )


__all__ = [
    "CapabilityHint",
    "ConstraintCode",
    "IntentKind",
    "RequestedEffect",
    "RiskHint",
    "TaskIntentAnalyzer",
    "TaskIntentEnvelope",
    "TaskShape",
]
