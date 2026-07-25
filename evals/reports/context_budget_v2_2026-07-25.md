# Sage Context Budget v2 消融报告

- Source commit: `6ec7d6fca8a7c3d52d09cf31791f1e171e9f03db`
- Cases: 12
- Scope: Deterministic context-cost and safety invariants; not an LLM answer-quality benchmark.

| Variant | Total input | Reduction vs A0 | Peak input | Compactions | User | Tool pairs | Decision | Artifact |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A0_baseline | 2177535 | 0.00% | 50530 | 0 | 100% | 100% | 100% | 0% |
| A1_budget | 2177535 | 0.00% | 50530 | 0 | 100% | 100% | 100% | 0% |
| A2_compact | 1861774 | 14.50% | 29433 | 7 | 100% | 100% | 100% | 0% |
| A3_full | 1861774 | 14.50% | 29433 | 7 | 100% | 100% | 100% | 100% |

## 阈值结论

固定扫描推荐 `working_set=32000`、`keep=12000`，selection score `0.095658`。

本报告只证明机制成本与安全不变量，不证明自然语言回答质量。
