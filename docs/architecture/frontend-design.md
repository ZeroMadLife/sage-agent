# 前端架构设计 — Vue3 Web + UniApp Android 双端方案

> **状态：待确认** — 本文锁定前端技术栈与双端架构，作为 Phase 4 开发依据。

## 一、技术选型

### 为什么 Vue3 + UniApp 而非 Streamlit

报告原方案用 Streamlit 快速验证，但存在三个问题：
1. **Streamlit 是 Python 内嵌前端**，无法独立部署为真正的 Web 产品，秋招"已上线"的说服力弱
2. **无法做 Android 端**——旅游场景移动端是刚需（行程中查看、离线缓存、地图导航）
3. **Vue3 + UniApp 共享 TypeScript 技术栈**，一套语言覆盖双端，学习成本可控

### 双端定位

| 端 | 技术 | 定位 | 优先级 |
|----|------|------|--------|
| **Web 端** | Vue3 + Vite + Pinia | 功能完整的桌面浏览器体验，面试演示主力 | P0（Phase 4 先行） |
| **Android 端** | UniApp（Vue3 语法） | 移动场景：行程中查看、地图导航、离线缓存 | P1（Phase 4 后半） |

> **策略：** Phase 4 前半段完成 Vue3 Web 端（验证产品逻辑 + API 联调），后半段用 UniApp 复用核心逻辑做 Android 端。如果时间紧，UniApp 端可推迟到 Phase 5。

---

## 二、Vue3 Web 端架构

### 技术栈

| 层 | 选型 | 理由 |
|----|------|------|
| 框架 | Vue 3.5 (Composition API) | 生态成熟，`<script setup>` 语法简洁 |
| 构建 | Vite 6 | 秒级热更新，生产构建优化 |
| 状态 | Pinia | Vue3 官方推荐，TypeScript 友好 |
| 路由 | Vue Router 4 | 标准方案 |
| 样式 | Tailwind CSS 4 | 原子化CSS，快速开发响应式布局 |
| HTTP | axios + 原生 WebSocket | REST 请求 + AI 流式输出 |
| 地图 | 高德地图 JS API 2.0 | 与后端高德MCP数据源一致 |
| 图表 | ECharts 5 | 预算仪表盘（环形图）+ 行程可视化 |
| 类型 | TypeScript 5 | 全量类型安全 |
| UI组件 | 自研 + 少量 Headless UI | 避免重型UI库，保持轻量 |

### 目录结构

```
frontend/
├── src/
│   ├── api/                    # API 请求层
│   │   ├── client.ts           # axios 实例 + 拦截器
│   │   ├── chat.ts             # 聊天相关 API
│   │   ├── session.ts          # 会话管理 API
│   │   └── itinerary.ts        # 行程管理 API
│   ├── stores/                 # Pinia 状态管理
│   │   ├── chat.ts             # 聊天状态（消息列表/流式输出）
│   │   ├── session.ts          # 会话状态
│   │   ├── itinerary.ts        # 行程状态（时间轴/路线/预算）
│   │   └── user.ts             # 用户状态（偏好/记忆）
│   ├── components/             # 可复用组件
│   │   ├── chat/               # 聊天界面组件
│   │   │   ├── ChatWindow.vue  # 消息列表 + 流式输出
│   │   │   ├── MessageBubble.vue
│   │   │   └── InputBar.vue    # 输入框 + 快捷指令
│   │   ├── itinerary/          # 行程可视化组件
│   │   │   ├── Timeline.vue    # 时间轴展示
│   │   │   ├── MapView.vue     # 高德地图路线
│   │   │   └── SpotCard.vue    # 景点卡片
│   │   ├── budget/             # 预算组件
│   │   │   ├── BudgetDonut.vue # 环形图仪表盘
│   │   │   └── BudgetDetail.vue
│   │   └── common/             # 通用组件
│   ├── views/                  # 页面
│   │   ├── ChatView.vue        # 主聊天页
│   │   ├── ItineraryView.vue   # 行程详情页
│   │   └── HistoryView.vue     # 历史会话页
│   ├── composables/            # 组合式函数
│   │   ├── useWebSocket.ts     # WS 流式连接管理
│   │   ├── useStreaming.ts     # AI 回复流式渲染
│   │   └── useMap.ts           # 高德地图封装
│   ├── types/                  # TypeScript 类型定义
│   │   ├── chat.ts
│   │   ├── itinerary.ts
│   │   └── api.ts              # 与后端 Pydantic 模型对齐
│   ├── router/                 # 路由配置
│   ├── App.vue
│   └── main.ts
├── index.html
├── vite.config.ts
├── tsconfig.json
├── tailwind.config.ts
├── package.json
└── .env                        # VITE_API_BASE_URL 等
```

### 核心交互流程

```
用户输入 → InputBar.vue
    ↓
ChatView.vue → POST /api/v1/chat (创建对话)
    ↓
useWebSocket.ts → WS /api/v1/chat/{session_id}/stream
    ↓
Supervisor 处理中 → 流式返回 Agent 执行进度
    ↓ "规划Agent正在搜索景点..."
    ↓ "预算Agent正在计算花费..."
useStreaming.ts → 逐 token 渲染到 ChatWindow.vue
    ↓
最终结构化行程 → ItineraryView.vue
    ├── Timeline.vue（左侧时间轴）
    ├── MapView.vue（右侧高德地图路线）
    └── BudgetDonut.vue（底部预算仪表盘）
    ↓
用户一键调整 → 触发反馈循环 → 重新 WS 流式
```

### 关键设计决策

**1. 流式输出用 WebSocket 而非 SSE**
- WebSocket 支持双向通信——用户可以在Agent执行中发送"暂停"或"调整"指令
- LangGraph 的 `interrupt()` 机制需要双向通道支持 Human-in-the-loop

**2. 类型定义与后端 Pydantic 对齐**
- `frontend/src/types/` 中的 TypeScript interface 与后端 Pydantic 模型一一对应
- 后端 FastAPI 自动生成 OpenAPI schema，前端用 `openapi-typescript` 自动生成类型

**3. 地图组件用高德 JS API 而非 Leaflet**
- 与后端高德 MCP Server 数据源一致，坐标系统一（GCJ-02）
- 支持路线规划、POI 搜索的客户端可视化

---

## 三、UniApp Android 端架构

### 技术栈

| 层 | 选型 | 理由 |
|----|------|------|
| 框架 | UniApp (Vue3 语法) | 一套代码编译 Android APK，学习成本低 |
| UI | uni-ui + 自定义组件 | 官方组件库兼容性好 |
| 状态 | Pinia | 与 Web 端共享 store 逻辑 |
| 网络 | uni.request + WebSocket | UniApp 原生 API |
| 地图 | nvue map 组件 | 原生渲染性能，支持高德地图 |
| 类型 | TypeScript | 与 Web 端共享类型定义 |

### 目录结构

```
mobile/
├── src/
│   ├── api/                    # 与 Web 端共享 API 层逻辑
│   ├── stores/                 # 与 Web 端共享 Pinia store
│   ├── types/                  # 与 Web 端共享类型定义
│   ├── components/             # UniApp 组件
│   │   ├── chat/
│   │   ├── itinerary/
│   │   └── budget/
│   ├── pages/                  # UniApp 页面（对应 Web 端 views）
│   │   ├── chat/index.vue
│   │   ├── itinerary/index.vue
│   │   └── history/index.vue
│   ├── static/                 # 静态资源
│   ├── App.vue
│   ├── main.ts
│   ├── manifest.json           # UniApp 配置（App ID/权限/SDK）
│   └── pages.json              # 页面路由配置
├── package.json
└── tsconfig.json
```

### 双端代码复用策略

```
shared/                        # 可复用层（Web + UniApp 共享）
├── types/                     # TypeScript 类型定义
│   ├── chat.ts
│   ├── itinerary.ts
│   └── api.ts
├── api/                       # API 请求抽象
│   ├── client.ts              # 统一接口（Web用axios, UniApp用uni.request）
│   └── endpoints.ts           # API 端点定义
└── stores/                    # Pinia store（业务逻辑共享）
    ├── chat.ts
    └── itinerary.ts
```

**复用原则：**
- 类型定义 100% 共享
- API 端点定义共享，底层 HTTP 客户端各自适配（axios vs uni.request）
- Pinia store 业务逻辑共享，UI 组件各自实现（Vue3 vs UniApp 组件API差异）

### Android 端独有能力

| 能力 | 实现方式 | 价值 |
|------|----------|------|
| 离线缓存 | uni.setStorage + 行程数据本地化 | 景区弱网刚需 |
| 推送通知 | UniApp push + 天气预警 | 异常天气主动提醒 |
| 定位导航 | uni.getLocation + 高德导航 SDK | 行程中实时导航 |
| 相册 | uni.chooseImage | V2.0 智能相册基础 |

---

## 四、前后端接口契约

### REST API

| 端点 | 方法 | 用途 |
|------|------|------|
| `/api/v1/chat` | POST | 创建新对话，返回 session_id |
| `/api/v1/chat/{session_id}/stream` | WS | 流式获取 Agent 回复 |
| `/api/v1/sessions` | GET | 获取用户历史会话列表 |
| `/api/v1/sessions/{id}` | GET | 获取会话详情 |
| `/api/v1/itineraries` | GET/POST | 行程列表/创建行程 |
| `/api/v1/itineraries/{id}` | GET/PUT | 行程详情/修改行程 |
| `/api/v1/users/me` | GET | 获取当前用户信息+偏好 |

### WebSocket 消息格式

```typescript
// 客户端 → 服务端
{ "type": "chat", "content": "周末去杭州2日游预算500元" }
{ "type": "adjust", "action": "replace_spot", "spot_id": "xxx" }
{ "type": "interrupt", "action": "approve" | "reject" }

// 服务端 → 客户端
{ "type": "progress", "agent": "planning", "message": "正在搜索景点..." }
{ "type": "token", "content": "根据" }  // 流式 token
{ "type": "tool_call", "tool": "search_attractions", "args": {...} }
{ "type": "result", "itinerary": {...}, "budget": {...} }
{ "type": "interrupt_request", "tool": "book_hotel", "detail": {...} }
{ "type": "error", "message": "...", "recoverable": true }
```

### 数据模型对齐

后端 Pydantic 模型 → 前端 TypeScript interface（自动生成）：

```typescript
// 对应后端 TravelState 的 final_response 结构
interface ItineraryResponse {
  destination: string
  dates: { start: string; end: string }
  days: ItineraryDay[]
  budget: BudgetSummary
  weather: WeatherInfo
}

interface ItineraryDay {
  date: string
  spots: ItinerarySpot[]
  meals: Meal[]
  transport: TransportInfo[]
  total_cost: number
}

interface BudgetSummary {
  total: number
  spent: number
  categories: {
    transport: number
    accommodation: number
    food: number
    tickets: number
    misc: number
  }
  over_budget: boolean
}
```

---

## 五、开发顺序与裁剪策略

### Phase 4 内部分解

| 子阶段 | 时间 | 任务 | 交付 |
|--------|------|------|------|
| 4a | Week 7 前半 | FastAPI REST + WS + 类型导出 | API 可用，OpenAPI schema 生成 |
| 4b | Week 7 后半 | Vue3 Web 端骨架 + 聊天界面 | 聊天流式输出可用 |
| 4c | Week 8 前半 | Vue3 行程可视化 + 预算仪表盘 | 完整 Web 端可演示 |
| 4d | Week 8 后半 | UniApp Android 端 + 集成测试 | Android APK 可运行 |

### 裁剪优先级（如果时间不够）

1. **必做：** Vue3 Web 端聊天 + 行程展示 + 预算仪表盘（M4 验收最低要求）
2. **应做：** UniApp Android 端基础版（聊天 + 行程查看）
3. **可裁：** UniApp 离线缓存、推送通知、定位导航（推迟到 V2.0）
4. **可裁：** Vue3 高德地图路线可视化（先用时间轴替代，地图后续补）

---

## 六、部署架构

```
                    ┌─────────────┐
                    │   用户浏览器  │  ← Vue3 Web 端
                    └──────┬──────┘
                           │ HTTPS/WSS
                    ┌──────↓──────┐
                    │    Nginx     │  ← 反向代理 + SSL + 静态文件
                    └──┬───────┬───┘
           静态文件 ←──┘       └──→ API 反代
     ┌─────────────┐         ┌──────↓──────┐
     │ Vue3 dist/  │         │   FastAPI   │
     │ (Nginx托管)  │         │   (Port 8000)│
     └─────────────┘         └─────────────┘

                    ┌─────────────┐
                    │  Android App │  ← UniApp APK
                    │  (独立安装)   │
                    └──────┬──────┘
                           │ HTTPS
                    ┌──────↓──────┐
                    │   FastAPI    │  ← 直连后端 API
                    └─────────────┘
```

- Vue3 构建产物（`dist/`）由 Nginx 托管为静态文件
- UniApp 编译为 APK，独立安装，通过 HTTPS 直连后端 API
- Nginx 同时代理 `/api/*` 到 FastAPI
