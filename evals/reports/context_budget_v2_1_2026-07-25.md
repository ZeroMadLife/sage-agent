# Sage Context Governance v2.1 消融报告

- Source commit: `323c8a6409b9f781133df6950f8172c701922ecf`
- Cases: 13
- Scope: Deterministic context-cost and safety invariants; not an LLM answer-quality benchmark.

| Variant | Total input | Reduction vs A0 | Peak input | Prunes | Compactions | User | Tool pairs | Decision | Artifact |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A0_previous_offload | 2779847 | 0.00% | 50612 | 0 | 0 | 100% | 100% | 100% | 0% |
| A1_offload_4k | 2779847 | 0.00% | 50612 | 0 | 0 | 100% | 100% | 100% | 0% |
| A2_recoverable_prune | 2244450 | 19.26% | 49830 | 24 | 0 | 100% | 100% | 100% | 0% |
| A3_semantic_compact | 2083494 | 25.05% | 31016 | 24 | 2 | 100% | 100% | 100% | 0% |
| A4_full | 2083494 | 25.05% | 31016 | 24 | 2 | 100% | 100% | 100% | 100% |

## 阈值结论

固定扫描推荐 `working_set=32000`、`keep=12000`，selection score `0.239884`。

本报告只证明机制成本与安全不变量，不证明自然语言回答质量。
