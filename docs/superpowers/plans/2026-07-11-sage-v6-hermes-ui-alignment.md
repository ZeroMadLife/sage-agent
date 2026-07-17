# Sage V6 Hermes Studio Workbench Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Independently implement the Hermes Studio-style coding workflow with Sage branding, Chinese defaults, complete responsive access, token context states, and Memory proposal review.

**Architecture:** New leaf components and domain stores isolate Context and Memory behavior from the existing large coding store. The Integration Agent alone composes them into `CodingView.vue`, preserving typed event order and the existing `run_finished` terminal boundary.

**Tech Stack:** Vue 3, Pinia, TypeScript, Lucide Vue, CSS custom properties, vue-i18n, Vitest, Vue Test Utils, Playwright.

---

## Clean-Room Rule

The implementation reference is the approved Sage design and observable behavior matrix. Implementation agents must not copy Hermes Studio source, CSS, class names, text, fonts, images, logos, or exact visual values. They must not read `/tmp/sage-hermes-studio` while implementing. Sage uses its own assets, Chinese copy, `--sage-*` tokens, and Lucide icons.

Hermes Studio is BSL 1.1; commercial use requires a separate license until 2029-05-10. This plan delivers workflow parity, not source parity.

## File Structure

- Create `frontend/src/styles/coding-tokens.css`.
- Create `frontend/src/i18n/index.ts`, `locales/zh-CN.ts`, and `locales/en-US.ts`.
- Create `frontend/src/types/codingContext.ts`, `api/codingContext.ts`, `stores/codingContext.ts`.
- Create `frontend/src/types/codingMemory.ts`, `api/codingMemory.ts`, `stores/codingMemory.ts`.
- Create `frontend/src/components/coding/layout/CodingWorkspaceHeader.vue`.
- Create `frontend/src/components/coding/layout/CodingInspectorPanel.vue`.
- Create `frontend/src/components/coding/sidebar/CodingSessionRail.vue`.
- Create `frontend/src/components/coding/context/CodingContextUsage.vue`.
- Create `frontend/src/components/coding/context/CodingCompactionNotice.vue`.
- Create `frontend/src/components/coding/memory/CodingMemoryPanel.vue`.
- Create `frontend/src/components/coding/memory/CodingMemoryProposalCard.vue`.
- Create `frontend/src/components/coding/files/CodingDiffPanel.vue`.
- Create `frontend/src/components/coding/runs/CodingRunPanel.vue`.
- Integration Agent only: modify root view/store/event/types/index/package files listed in the orchestration plan.

### Task 1: Sage Tokens and Chinese-First Localization

**Files:**
- Create: `frontend/src/styles/coding-tokens.css`
- Create: `frontend/src/i18n/index.ts`
- Create: `frontend/src/i18n/locales/zh-CN.ts`
- Create: `frontend/src/i18n/locales/en-US.ts`
- Integration Agent modify: `frontend/src/main.ts`
- Integration Agent modify: `frontend/package.json`

- [ ] **Step 1: Add localization tests**

Create `frontend/src/i18n/index.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { createSageI18n } from './index'

describe('Sage coding locale', () => {
  it('uses Simplified Chinese as locale and fallback', () => {
    const i18n = createSageI18n()
    expect(i18n.global.locale.value).toBe('zh-CN')
    expect(i18n.global.fallbackLocale.value).toBe('zh-CN')
    expect(i18n.global.t('coding.actions.stop')).toBe('停止运行')
  })
})
```

- [ ] **Step 2: Install and configure `vue-i18n`**

```bash
cd frontend && npm install vue-i18n@^11
```

Create `frontend/src/i18n/index.ts`:

```ts
import { createI18n } from 'vue-i18n'
import enUS from './locales/en-US'
import zhCN from './locales/zh-CN'

export function createSageI18n() {
  return createI18n({
    legacy: false,
    locale: 'zh-CN',
    fallbackLocale: 'zh-CN',
    messages: { 'zh-CN': zhCN, 'en-US': enUS },
  })
}
```

The Chinese dictionary must include sessions, files, changes, runs, memory, context, model, permission, plan, stop, compact, approve, reject, edit, loading, empty, failed, cancelled, and disconnected states.

- [ ] **Step 3: Add independent Sage tokens**

Create `frontend/src/styles/coding-tokens.css`:

```css
:root {
  --sage-canvas: #f5f7f5;
  --sage-surface: #ffffff;
  --sage-surface-muted: #eef2ef;
  --sage-text: #17201b;
  --sage-text-muted: #647169;
  --sage-border: #d7ded9;
  --sage-accent: #117a5b;
  --sage-info: #2563eb;
  --sage-success: #1f7a4d;
  --sage-warning: #a96612;
  --sage-error: #b93831;
  --sage-radius-sm: 4px;
  --sage-radius-md: 6px;
  --sage-radius-lg: 8px;
  --sage-icon-button: 36px;
}
```

- [ ] **Step 4: Run tests and commit**

```bash
cd frontend && npm run test -- --run src/i18n/index.test.ts
git add frontend/package.json frontend/package-lock.json frontend/src/i18n frontend/src/styles/coding-tokens.css frontend/src/main.ts
git commit -m "feat(sage-ui): add Chinese locale and design tokens"
```

### Task 2: Responsive Workbench Leaf Layout

**Files:**
- Create: `frontend/src/components/coding/layout/CodingWorkspaceHeader.vue`
- Create: `frontend/src/components/coding/layout/CodingInspectorPanel.vue`
- Create: `frontend/src/components/coding/sidebar/CodingSessionRail.vue`
- Create tests beside each component.

- [ ] **Step 1: Write component tests**

Cover:

```text
CodingWorkspaceHeader exposes session and inspector icon buttons with accessible Chinese labels
CodingSessionRail renders active/running/failed session states
CodingInspectorPanel exposes Files/Changes/Runs/Memory tabs
Inspector width clamps to 360-640 and persists as sage.coding.inspectorWidth
Mobile sheets close on Escape and restore focus to their trigger
```

- [ ] **Step 2: Implement layout contracts**

`CodingWorkspaceHeader` emits `open-sessions` and `open-inspector`. `CodingInspectorPanel` accepts `activeTab`, `open`, and `width`, then emits `update:activeTab`, `update:open`, and `update:width`. `CodingSessionRail` accepts session summaries and emits `select-session`/`new-session`.

Use these responsive rules:

```css
@media (min-width: 1280px) {
  .sage-workbench { grid-template-columns: 248px minmax(480px, 1fr) var(--inspector-width); }
}
@media (min-width: 960px) and (max-width: 1279px) {
  .sage-workbench { grid-template-columns: 248px minmax(0, 1fr); }
  .sage-inspector { position: fixed; inset: 0 0 0 auto; width: min(440px, 100vw); }
}
@media (max-width: 959px) {
  .sage-workbench { grid-template-columns: minmax(0, 1fr); }
  .sage-session-rail, .sage-inspector { position: fixed; inset: 0; }
}
```

Do not hide inaccessible functionality with `display:none` unless an equivalent sheet trigger is visible.

- [ ] **Step 3: Run tests and commit**

```bash
cd frontend && npm run test -- --run src/components/coding/layout src/components/coding/sidebar/CodingSessionRail.test.ts
git add frontend/src/components/coding/layout frontend/src/components/coding/sidebar/CodingSessionRail.vue frontend/src/components/coding/sidebar/CodingSessionRail.test.ts
git commit -m "feat(sage-ui): add responsive workbench shell"
```

### Task 3: Context Domain Store and Components

**Files:**
- Create: `frontend/src/types/codingContext.ts`
- Create: `frontend/src/api/codingContext.ts`
- Create: `frontend/src/stores/codingContext.ts`
- Create: `frontend/src/stores/codingContext.test.ts`
- Create: `frontend/src/components/coding/context/CodingContextUsage.vue`
- Create: `frontend/src/components/coding/context/CodingCompactionNotice.vue`
- Create tests beside both components.

- [ ] **Step 1: Define the exact frontend contract**

```ts
export type ContextLevel = 'normal' | 'budget' | 'snip' | 'compact' | 'high' | 'emergency'

export type ContextUsageSnapshot = {
  session_id: string
  run_id: string
  used_tokens: number
  model_limit_tokens: number
  output_reserve_tokens: number
  effective_limit_tokens: number
  usage_ratio: number
  level: ContextLevel
  estimated: boolean
  compactable: boolean
}

export type CompactionState = {
  compactionId: string
  status: 'idle' | 'running' | 'completed' | 'failed'
  beforeTokens: number
  afterTokens: number
  archivedItems: number
  reason: string
}
```

- [ ] **Step 2: Write reducer tests**

Cover stale-session event rejection, usage ratio from backend, estimated label, started/completed/failed transitions, old compaction ID rejection, failed compaction preserving last usage, reconnect snapshot, and manual compact disabled while a run is active.

- [ ] **Step 3: Implement domain store and API**

`codingContext.ts` exports `applyContextEvent(event, expectedSessionId)` and never changes the main coding store's `isThinking`. `codingContext.ts` API client implements GET context snapshot and POST manual compact with typed `409`/`422` errors.

- [ ] **Step 4: Implement usage and notice components**

Use a stable horizontal usage meter, not a viewport-scaled font. Show `used/effective` tokens, model limit, output reserve, estimated marker, and the backend level. Manual compact uses a `RefreshCw` Lucide icon with tooltip `整理上下文`.

Completed notice renders `上下文已整理 {before} -> {after}，归档 {count} 条记录`. Failed notice states that original context was preserved.

- [ ] **Step 5: Run tests and commit**

```bash
cd frontend && npm run test -- --run src/stores/codingContext.test.ts src/components/coding/context
git add frontend/src/types/codingContext.ts frontend/src/api/codingContext.ts frontend/src/stores/codingContext.ts frontend/src/stores/codingContext.test.ts frontend/src/components/coding/context
git commit -m "feat(sage-ui): show token context lifecycle"
```

### Task 4: Memory and Dream Domain Store

**Files:**
- Create: `frontend/src/types/codingMemory.ts`
- Create: `frontend/src/api/codingMemory.ts`
- Create: `frontend/src/stores/codingMemory.ts`
- Create: `frontend/src/stores/codingMemory.test.ts`

- [ ] **Step 1: Define proposal types**

```ts
export type MemoryEvidenceRef = {
  kind: 'user_statement' | 'approved_plan' | 'run_event'
  session_id: string
  run_id: string
  event_index: number
  path: string
  content_hash: string
}

export type MemoryChange = {
  change_id: string
  operation: 'add' | 'update' | 'merge' | 'archive'
  topic: string
  before: string
  after: string
  reason: string
  evidence_refs: MemoryEvidenceRef[]
  confidence: number
  flags: string[]
}

export type MemoryProposal = {
  proposal_id: string
  version: number
  base_revision: number
  status: 'pending' | 'approved' | 'rejected'
  changes: MemoryChange[]
}
```

- [ ] **Step 2: Write store tests**

Cover proposal deduplication, stale-session event rejection, reconnect recovery, selected candidate state, sensitive candidate unchecked by default, edit version increment, approve/reject idempotency, conflict error, rollback result, and background review not changing main run status.

- [ ] **Step 3: Implement API and store**

The API client implements list/get/edit/approve/reject/rollback endpoints from the approved design. The store always refreshes the proposal from REST after a mutation; WebSocket only marks a resource stale and triggers fetch.

- [ ] **Step 4: Run tests and commit**

```bash
cd frontend && npm run test -- --run src/stores/codingMemory.test.ts
git add frontend/src/types/codingMemory.ts frontend/src/api/codingMemory.ts frontend/src/stores/codingMemory.ts frontend/src/stores/codingMemory.test.ts
git commit -m "feat(sage-ui): add memory proposal state"
```

### Task 5: Memory Proposal and Inspector Panels

**Files:**
- Create: `frontend/src/components/coding/memory/CodingMemoryPanel.vue`
- Create: `frontend/src/components/coding/memory/CodingMemoryProposalCard.vue`
- Create: `frontend/src/components/coding/files/CodingDiffPanel.vue`
- Create: `frontend/src/components/coding/runs/CodingRunPanel.vue`
- Create tests beside each component.

- [ ] **Step 1: Write proposal card tests**

Cover action/topic/content, evidence links, source run, confidence, conflict badge, sensitive flag, checkbox defaults, edit mode, approve selected, reject all, busy state, API conflict, and accessible focus after resolution.

- [ ] **Step 2: Implement Memory panels**

The Memory tab contains a compact index/fact list and pending proposals. Do not nest cards: the inspector is unframed; each proposal is one bordered repeated item. Use `Pencil`, `Check`, `X`, `RotateCcw`, and `ShieldAlert` Lucide icons with tooltips.

- [ ] **Step 3: Move Run and Diff views into leaf panels**

Reuse existing store data and `CodingDiffDrawer` behavior. Support loading, empty, error, truncated, binary, and sensitive states. The desktop inspector uses panels; mobile may continue opening the existing diff drawer.

- [ ] **Step 4: Run tests and commit**

```bash
cd frontend && npm run test -- --run src/components/coding/memory src/components/coding/files/CodingDiffPanel.test.ts src/components/coding/runs/CodingRunPanel.test.ts
git add frontend/src/components/coding/memory frontend/src/components/coding/files/CodingDiffPanel.vue frontend/src/components/coding/files/CodingDiffPanel.test.ts frontend/src/components/coding/runs
git commit -m "feat(sage-ui): add memory and evidence inspector"
```

### Task 6: Integration into Existing Coding Workbench

**Files:**
- Integration Agent modify: `frontend/src/views/CodingView.vue`
- Integration Agent modify: `frontend/src/components/coding/composer/CodingComposer.vue`
- Integration Agent modify: `frontend/src/components/coding/sidebar/CodingSidebar.vue`
- Integration Agent modify: `frontend/src/stores/coding.ts`
- Integration Agent modify: `frontend/src/stores/codingEvents.ts`
- Integration Agent modify: `frontend/src/types/api.ts`
- Integration Agent modify: `frontend/src/components/coding/index.ts`
- Create: `frontend/src/views/CodingView.test.ts`

- [ ] **Step 1: Add integration tests before composition**

Cover:

```text
final does not refresh sessions or runs before run_finished
context events update context domain only
memory review events do not set isThinking
pending proposal badge opens Memory inspector
mobile header exposes session and inspector sheets
late event from old session is discarded
```

- [ ] **Step 2: Route new events to domain reducers**

The root event handler validates `session_id`, then dispatches context events to `applyContextEvent`, memory events to `applyMemoryEvent`, and existing run/tool events to `applyCodingEvent`. Preserve the current rule that only `run_finished` triggers run/session refresh.

- [ ] **Step 3: Replace the fixed character ring**

Remove `contextBudget = 60000` and the character percentage. Compose `CodingContextUsage` in the composer and use backend `compactable` plus root `isThinking` to enable manual compaction.

- [ ] **Step 4: Compose responsive layout**

Use the new session rail, header, central message/composer, and inspector. Existing `CodingMessageTurn`, approval, plan review, file tree, diff drawer, and run data remain functional.

- [ ] **Step 5: Run unit/build gates and commit**

```bash
cd frontend && npm run test -- --run
cd frontend && npm run build
git add frontend/src frontend/package.json frontend/package-lock.json
git commit -m "feat(sage-ui): integrate coding workbench alignment"
```

### Task 7: Playwright Desktop and Mobile Verification

**Files:**
- Create: `frontend/playwright.config.ts`
- Create: `frontend/e2e/fixtures/codingHarness.ts`
- Create: `frontend/e2e/coding-workspace.desktop.spec.ts`
- Create: `frontend/e2e/coding-workspace.mobile.spec.ts`
- Create: `frontend/e2e/coding-context.spec.ts`
- Create: `frontend/e2e/coding-memory.spec.ts`
- Create: `frontend/e2e/coding-run-diff.spec.ts`
- Create: `frontend/e2e/coding-locale.spec.ts`
- Integration Agent modify: `frontend/package.json`

- [ ] **Step 1: Install Playwright test tooling**

```bash
cd frontend && npm install -D @playwright/test@^1.60
cd frontend && npx playwright install chromium
```

- [ ] **Step 2: Build REST/WebSocket fixtures**

The fixture serves deterministic session, run, diff, context, memory, and proposal responses and drives typed WebSocket events in exact order. Do not call external providers.

- [ ] **Step 3: Implement desktop tests**

At 1440x900 verify three accessible regions, inspector resizing/persistence, tool/approval/diff sequence, context started/completed/failed, and Memory approval refresh.

- [ ] **Step 4: Implement mobile tests**

At 390x844 verify no horizontal scroll, no hidden inaccessible panels, session and inspector sheets, safe-area composer, readable longest Chinese labels, and no overlap during streaming/approval.

- [ ] **Step 5: Verify locale and screenshot stability**

First visit is Chinese. Locale switch persists. Screenshots compare only against approved Sage baselines and canvas/pixel checks prove the main workbench is nonblank.

- [ ] **Step 6: Run and commit**

```bash
cd frontend && npx playwright test
git add frontend/playwright.config.ts frontend/e2e frontend/package.json frontend/package-lock.json
git commit -m "test(sage-ui): cover workbench desktop and mobile"
```
