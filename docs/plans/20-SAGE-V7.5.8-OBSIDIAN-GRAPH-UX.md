# Sage V7.5.8 Obsidian 风格知识图谱体验

> 日期：2026-07-17
>
> 分支：`dev/sage-v7`
>
> 状态：已完成

## 1. 问题

Sage 已经具备 revision-bound Knowledge Graph、Louvain 社区、Sigma 渲染、节点详情和引用证据，但当前图谱仍偏向“把所有节点画出来”：社区内部过密、标签容易叠加，用户点击后只看到放大的节点团，难以理解全局稀疏度、局部连接和来源关系。

DeerFlow Harness 2.0 已经把 Knowledge 作为独立 Surface，并能冻结 workspace、page、node、revision 和 job 上下文。因此本阶段不再重建一套对话或运行状态，只把 Knowledge 主画布做成可阅读、可追溯的入口。

## 2. 设计目标

1. **全局先看结构**：默认显示社区分布、节点权重和少量代表标签，能看出知识域之间的疏密与桥接关系。
2. **悬停即看邻域**：悬停节点时保留一跳邻居和相关边，其他节点与边降噪，不要求先点击。
3. **点击进入证据链**：选中节点后稳定聚焦，并在 Details Dock 中查看 Wiki 正文、revision、来源证据与关系。
4. **布局稳定且可感知**：社区种子经过 ForceAtlas2 松弛后缓存；再次打开只进行 650ms 以内的轻量收敛，算法升级通过 cache version 显式失效；系统减少动态时不运行动画。
5. **渐进披露标签**：全局只显示一个社区代表节点；悬停只显示当前节点；选中后最多补充一个高价值邻居；来源文件默认不抢占标签空间。
6. **移动端保持任务等价**：小型图谱继续渲染 WebGL 画布，但全局态不常驻节点标签；超过移动端性能预算时再降级为可搜索节点列表。

## 3. 与 Obsidian / llm_wiki 的边界

本阶段参考 Obsidian 图谱的全局观察、局部聚焦和渐进标签体验，也参考 `nashsu/llm_wiki` 公开 README 中的图谱产品行为：类型/社区着色、按链接数缩放、悬停突出邻域、位置缓存和 Louvain 社区。

Sage 不复制 Obsidian 或 GPLv3 `llm_wiki` 的源码、CSS、资源、品牌和内部数据结构。图谱继续使用 Sage 自有 revision、citation、community 和 evidence 契约。

## 4. 本次切片

```text
KnowledgeGraph API
  -> KnowledgeGraphCanvas
      -> community seed positions
      -> ForceAtlas2 relaxation
      -> revision-bound layout cache
      -> always-visible neutral relation edges
      -> global representative labels
      -> hover / selected one-hop focus
  -> KnowledgeInspector
      -> canonical Wiki revision
      -> citation excerpt
      -> one-hop relations
```

本次不接完整 Knowledge Chat。右侧 Chat Dock 仍只展示冻结上下文；等 DeerFlow v2 的跨 Surface session/composer 契约稳定后，再把当前 node/page/revision 传入同一 Chat Harness。

## 5. 验收

- 真实桌面图谱无容器高度错误，社区之间有可观察间距。
- 全局态关系边以预混合中性灰持续可见，浅色和暗色主题不依赖 WebGL `rgba()` 透明度表现。
- 未选择节点时，只强制显示少量社区代表标签。
- 悬停节点时，一跳邻域保持清晰，非邻域节点与边明显降噪。
- 选中节点后相机平滑聚焦，退出聚焦恢复全局结构。
- `EVIDENCED_BY` 不再被渲染器全局隐藏；仅当来源节点筛选开启时进入画布，并以弱于 Wiki 关系的层级显示。
- 社区/类型图例与当前着色模式一致，不显示固定假图例。
- `390x844` 的小型图谱保持可缩放画布且不显示常驻标签；超过预算时使用节点列表降级。
- 组件测试、前端全量测试、生产构建和 `git diff --check` 通过。

## 6. 后续边界

- V7.5.9：Wiki 树、正文阅读与图谱之间的双向定位；revision diff。
- V7.6：Knowledge Chat Dock 接入 DeerFlow v2 session、timeline、composer 和 citation chips。
- V7.7：摄取、解析、综合、索引和图谱重建投影为统一 Harness stage；图谱洞察触发受控研究任务。
