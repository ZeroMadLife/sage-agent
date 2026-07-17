# Sage V7.2 Knowledge Platform 视觉设计产物

本目录保存 V7.2 知识平台的可追溯设计产物：

- `sage-knowledge-platform-architecture.py`：系统架构图源码；
- `sage-knowledge-platform-architecture.png/svg`：系统架构图；
- `sage-knowledge-workbench-concept.png`：GPT Image CLI 生成的高保真视觉概念基线；
- `sage-knowledge-workbench-concept-mentalout.png`：mentalout 网页工作台生成的 2K 视觉概念；
- `visual-concept-prompt.txt`：可复用、无凭据的生图提示词；
- `sage-knowledge-workbench.html`：可交互、可响应式的前端设计稿；
- `screenshots/`：三视口验收截图。

设计稿只定义 V7.2-P2.2 至 P2.4 的产品与视觉基线，不是已交付功能清单。

## 本地预览

```bash
python3 -m http.server 5196 --directory docs/assets/v7-2-knowledge-platform
```

打开 `http://127.0.0.1:5196/sage-knowledge-workbench.html`。原型支持图谱节点详情、社区/类型切换、自动沉淀撤销、审核入口和摄取任务展开。
