# AI Travel Planning UX Benchmark

> Purpose: turn DeepTrip / Trip.com / Ctrip AI itinerary references into concrete Phase 4 UX requirements for TourSwarm. This is a product and frontend design note, not a Phase 3 memory-system requirement.

## Inputs

- Local screenshot: `/Users/zeromadlife/.codex/attachments/e65858aa-5751-4f6e-b44d-3f32ad8e610a/image-1.png`
- Existing local research: `Kimi_Agent_多智能体协作平台/research/multiagent_dim04.md`
- Existing frontend plan: `docs/architecture/frontend-design.md`
- Public references:
  - 同程旅行官网 DeepTrip / 程心 AI入口: https://www.ly.com/
  - 新华网，同程旅行 x DeepSeek AI 旅行助手: https://www.news.cn/travel/20250310/f50a93eb972540bab5469976c619ecd6/c.html
  - DeepTrip App Store listing: https://apps.apple.com/hk/app/deeptrip/id6747013871
  - Trip.com Trip Planner: https://www.trip.com/tripplanner/
  - Trip.com Trip Planner blog: https://www.trip.com/blog/trip-planner-tool/
  - Ctrip Trip Planner CN page: https://www.ctrip.com/tripplanner/

## Benchmark Summary

| Product | Strong UX Pattern | What It Means For TourSwarm |
|---------|-------------------|-----------------------------|
| DeepTrip / 同程程心 AI | Natural-language request to itinerary, booking and decision flow | Do not stop at chat output; produce an editable plan with transport, hotel, attraction and booking-ready slots. |
| DeepTrip / 同程程心 AI | Understands implicit needs from budget, timing, crowding and history | Surface why a recommendation fits: budget, crowd avoidance, preference memory, weather and route logic. |
| DeepTrip App | Route, hotel, transport, multilingual and real-time adjustment claims | Keep MVP scoped, but design data model and UI slots for route/hotel/transport modules instead of a text-only itinerary. |
| Trip.com / Ctrip Trip Planner | Starts from destination, duration and travel style; supports canvas-style editing | Phase 4 should use a planning workbench: quick inputs first, then an editable itinerary canvas. |
| Ctrip AI assistant screenshot | Agent progress on the left, itinerary editor on the right | Use a split-pane layout on desktop: process visibility + structured itinerary workspace. |

## Ctrip Screenshot Observations

The screenshot shows a mature pattern for AI itinerary planning:

- Left pane: an `AI助手` panel with the original user request and a step checklist. The assistant marks progress through requirement understanding, destination lookup, attraction lookup, hotel selection and day-by-day scheduling.
- Right pane: an `行程详情` workspace with tabs for overview, unscheduled items, day 1, day 2 and notes.
- The generated itinerary is not a flat answer. It is editable through inline actions such as `+ 地点`, `+ 飞机/火车`, `+ 自定义活动`.
- Cards combine concrete entities with operational details: location category chips, rating, images, recommended reason, opening hours, route duration and route distance.
- The UI acknowledges user edits with lightweight feedback. The screenshot shows a bottom toast-like state: `已选择3个地点`.
- The itinerary has “choose where to stay” and “day route” sections, which means lodging is part of planning context, not an afterthought.

## TourSwarm UX Direction

### 1. First Screen: Planning Workbench, Not Marketing

The first product screen should be the actual planning workspace:

- Header: trip title, date selector, save/share controls.
- Left rail: AI conversation, agent progress, recoverable errors and memory hints.
- Main canvas: itinerary tabs, day timeline, route segments, budget and edit actions.
- Right-side details or drawer: selected spot / hotel / transport details, reasons and alternatives.

This matches TourSwarm's architecture better than a pure chatbot because LangGraph already emits agent progress and structured state.

### 2. Make Agent Work Visible

Map backend events from `docs/architecture/frontend-design.md` to visible UI states:

| Backend Event | UI Surface |
|---------------|------------|
| `progress` | Checklist row in the AI assistant pane |
| `tool_call` | Compact activity row, e.g. searching weather or attractions |
| `token` | Streaming explanation only when useful |
| `result` | Update itinerary canvas and budget summary |
| `error` with `recoverable=true` | Inline warning with retry or continue action |
| `interrupt_request` | Confirmation modal or inline approval row |

The user should be able to tell whether the system is searching, reasoning, validating, replanning or waiting for confirmation.

### 3. Prefer Structured Editing Over Chat Corrections

Chat remains the natural input channel, but common itinerary changes should be direct controls:

- Replace spot
- Remove spot
- Add spot
- Add meal
- Add transport
- Lock this item
- Reorder within day
- Replan this day only
- Optimize route
- Lower budget

Each direct edit should also create a compact chat/system event so the AI context stays synchronized.

### 4. Turn Technical Differentiators Into User-Facing Signals

TourSwarm should make its backend strengths visible:

| Backend Capability | User-Facing Signal |
|--------------------|--------------------|
| Memory system | "已记住：偏好海鲜 / 预算敏感 / 喜欢自然风光" |
| Budget Agent | Budget bar, category chips and over-budget warning |
| Weather Agent | Weather badge per day plus adjustment suggestion |
| Recommend Agent | Recommendation reason and alternatives |
| Planning Agent | Timeline, route sequence and estimated duration |
| Future verifier | "已检查：时间 / 预算 / 路线 / 开放时间" |

Avoid presenting these as technical labels in the main UI. Use plain travel language.

### 5. MVP Component Requirements

For Phase 4 Web MVP, prioritize these components:

- `PlanningShell`: desktop split-pane layout and mobile stacked layout.
- `AgentProgressPanel`: request summary, checklist, tool activity and recoverable errors.
- `ItineraryTabs`: overview, unscheduled, day tabs and notes.
- `DayTimeline`: fixed day route with time, POI card, transport segment and cost.
- `SpotCard`: image, name, rating, ticket price, recommended reason and actions.
- `BudgetBar`: total budget, spent, remaining and category breakdown.
- `EditActionBar`: add spot, add transport, custom activity and route optimization.
- `MemoryHint`: subtle preference chips sourced from Phase 3 memory.

Cuttable for the first slice:

- Full map rendering.
- Hotel booking checkout.
- Multilingual support.
- Social share image generation.

Do not cut:

- Agent progress visibility.
- Editable itinerary tabs.
- Budget summary.
- Clear empty/loading/error states.

## Phase 4 Acceptance Additions

Add these checks to the frontend acceptance criteria:

- A user can see which agent step is currently running.
- A generated itinerary appears as structured cards/timeline, not only markdown text.
- A user can add or remove a spot without typing a new full prompt.
- The UI shows memory-derived preferences when available.
- The UI can display recoverable tool failures without blocking the final itinerary.
- Desktop has a split-pane workbench; mobile keeps the assistant and itinerary accessible through tabs or stacked sections.

## Product Positioning Takeaway

DeepTrip and Ctrip are converging on the same product truth: AI travel planning is not a chat feature, it is a decision workspace. TourSwarm should use chat as the entry point, but the durable value should live in an editable, budget-aware, route-aware itinerary canvas.
