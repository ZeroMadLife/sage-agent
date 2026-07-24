import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { CodingRunAuditSummary } from '../../../types/api'
import type { TimelineTurn } from '../../../stores/codingTimeline'
import CodingRunTrace from './CodingRunTrace.vue'

const audit: CodingRunAuditSummary = {
  run_id: 'run-a',
  status: 'completed',
  headline: '运行完成 · 2 项工具 · 修改 1 个文件',
  tool_count: 2,
  completed_tool_count: 2,
  failed_tool_count: 0,
  approval_count: 1,
  duration_ms: 2250,
  changed_files: ['src/app.ts'],
  steps: [
    {
      tool: 'read_file',
      status: 'completed',
      action_summary: '读取 README.md',
      result_summary: '执行完成',
      duration_ms: 250,
      arguments_preview: '{"path":"README.md"}',
      result_preview: '已读取文件内容（摘要不展示正文）',
      arguments_truncated: false,
      result_truncated: false,
    },
    {
      tool: 'run_shell',
      status: 'completed',
      action_summary: '执行 npm test',
      result_summary: '退出码 0',
      duration_ms: 2000,
      arguments_preview: '{"command":"npm test"}',
      result_preview: 'exit_code: 0\n12 passed',
      arguments_truncated: false,
      result_truncated: false,
    },
  ],
}

describe('CodingRunTrace', () => {
  it('projects durable timeline facts into explicit execution stage cards', () => {
    const turn: TimelineTurn = {
      id: 'turn:run-stage',
      run_id: 'run-stage',
      user: { content: '检查 README 并运行测试', event_id: 'evt-user', timestamp: '2026-07-24T08:00:00Z' },
      assistant: { content: '检查完成。', event_id: 'evt-answer', timestamp: '2026-07-24T08:00:04Z' },
      context: [{ id: 'evt-context', type: 'context_snapshot', status: 'completed', timestamp: '2026-07-24T08:00:01Z', payload: {} }],
      model: [{ id: 'evt-model', type: 'model_requested', status: 'completed', timestamp: '2026-07-24T08:00:02Z', payload: {} }],
      tools: [{ id: 'evt-tool', tool: 'run_shell', args: { command: 'npm test' }, status: 'completed', result: 'exit_code: 0', is_error: false }],
      approvals: [],
      memory: [],
      agents: [],
      system: [],
      terminal: { event_id: 'evt-terminal', session_id: 'session-a', run_id: 'run-stage', sequence: 6, timestamp: '2026-07-24T08:00:04Z', kind: 'terminal', status: 'completed', payload: { type: 'final' } },
    }
    const wrapper = mount(CodingRunTrace, {
      props: { runId: 'run-stage', tools: turn.tools, turn, audit: { ...audit, approval_count: 0 } },
    })

    expect(wrapper.findAll('.run-stage-card')).toHaveLength(5)
    expect(wrapper.get('[data-stage="context"]').text()).toContain('输入与上下文')
    expect(wrapper.get('[data-stage="model"]').text()).toContain('1 轮模型请求')
    expect(wrapper.get('[data-stage="tools"]').text()).toContain('run_shell')
    expect(wrapper.get('[data-stage="approval"]').text()).toContain('未触发')
    expect(wrapper.get('[data-stage="answer"]').text()).toContain('回答已写入 timeline')
  })

  it('uses the persisted audit summary when a legacy timeline omitted tool events', () => {
    const turn: TimelineTurn = {
      id: 'turn:legacy-audit',
      run_id: 'legacy-audit',
      user: { content: '运行测试', event_id: 'evt-user', timestamp: '2026-07-24T08:00:00Z' },
      assistant: { content: '测试通过。', event_id: 'evt-answer', timestamp: '2026-07-24T08:00:03Z' },
      context: [],
      model: [],
      tools: [],
      approvals: [],
      memory: [],
      agents: [],
      system: [],
      terminal: { event_id: 'evt-terminal', session_id: 'session-a', run_id: 'legacy-audit', sequence: 3, timestamp: '2026-07-24T08:00:03Z', kind: 'terminal', status: 'completed', payload: { type: 'final' } },
    }
    const wrapper = mount(CodingRunTrace, {
      props: { runId: 'legacy-audit', tools: [], turn, audit },
    })

    expect(wrapper.get('[data-stage="tools"]').text()).toContain('2 项 · read_file、run_shell')
    expect(wrapper.get('[data-stage="tools"]').classes()).toContain('completed')
    expect(wrapper.get('[data-stage="approval"]').text()).toContain('1 次审批事件')
    expect(wrapper.get('[data-stage="model"]').text()).toContain('未记录')
    expect(wrapper.get('[data-stage="model"]').text()).toContain('Timeline 未记录模型事件')
  })

  it('does not call missing model or answer events completed when the terminal record exists', () => {
    const turn: TimelineTurn = {
      id: 'turn:terminal-only',
      run_id: 'terminal-only',
      user: { content: '检查状态', event_id: 'evt-user', timestamp: '2026-07-24T08:00:00Z' },
      assistant: null,
      context: [],
      model: [],
      tools: [],
      approvals: [],
      memory: [],
      agents: [],
      system: [],
      terminal: { event_id: 'evt-terminal', session_id: 'session-a', run_id: 'terminal-only', sequence: 2, timestamp: '2026-07-24T08:00:01Z', kind: 'terminal', status: 'completed', payload: { type: 'final' } },
    }
    const wrapper = mount(CodingRunTrace, {
      props: { runId: 'terminal-only', tools: [], turn },
    })

    expect(wrapper.get('[data-stage="model"]').text()).toContain('未记录')
    expect(wrapper.get('[data-stage="answer"]').text()).toContain('未记录')
    expect(wrapper.get('[data-stage="answer"]').classes()).toContain('unrecorded')
  })

  it('summarizes concrete actions and expands bounded shell output on demand', async () => {
    const wrapper = mount(CodingRunTrace, {
      props: { runId: 'run-a', tools: [], audit },
    })

    expect(wrapper.get('details').attributes('open')).toBeUndefined()
    expect(wrapper.get('summary').text()).toContain('读取 README.md、执行 npm test')
    expect(wrapper.get('summary').text()).toContain('2s')
    expect(wrapper.findAll('details')).toHaveLength(1)

    await wrapper.get('summary').trigger('click')

    expect(wrapper.get('details').attributes('open')).toBe('')
    expect(wrapper.findAll('.trace-step')).toHaveLength(2)
    expect(wrapper.text()).toContain('退出码 0')
    expect(wrapper.text()).toContain('src/app.ts')
    expect(wrapper.find('.step-output').exists()).toBe(false)
    expect(wrapper.get('.step-toggle').attributes('aria-expanded')).toBe('false')

    await wrapper.get('.step-toggle').trigger('click')

    expect(wrapper.get('.step-toggle').attributes('aria-expanded')).toBe('true')
    expect(wrapper.get('.step-output').text()).toContain('$ npm test')
    expect(wrapper.get('.step-output').text()).toContain('12 passed')
  })

  it('compresses repeated tool discovery and prioritizes real commands and files', async () => {
    const discoveryAudit: CodingRunAuditSummary = {
      ...audit,
      headline: '运行完成 · 5 项工具',
      tool_count: 5,
      steps: [
        ...Array.from({ length: 2 }, (_, index) => ({
          tool: 'tool_search',
          status: 'completed',
          action_summary: '调用 tool_search',
          result_summary: '执行完成',
          duration_ms: 100 + index,
          arguments_preview: `{"query":"${index ? 'shell' : 'files'}"}`,
          result_preview: '[]',
          arguments_truncated: false,
          result_truncated: false,
        })),
        audit.steps[0],
        audit.steps[1],
        {
          tool: 'agent',
          status: 'completed',
          action_summary: '子任务 执行',
          result_summary: '执行完成',
          duration_ms: 7,
          arguments_preview: '{"description":"探索内部工具实现"}',
          result_preview: '{"status":"started"}',
          arguments_truncated: false,
          result_truncated: false,
        },
      ],
    }
    const wrapper = mount(CodingRunTrace, {
      props: { runId: 'run-discovery', tools: [], audit: discoveryAudit },
    })

    expect(wrapper.get('summary').text()).toContain('读取 README.md、执行 npm test')
    expect(wrapper.get('summary').text()).not.toContain('tool_search')

    await wrapper.get('summary').trigger('click')

    expect(wrapper.get('.trace-discovery').text()).toContain('工具探索 · 2 次')
    expect(wrapper.findAll('.trace-step')).toHaveLength(3)
    expect(wrapper.text()).toContain('子任务 探索内部工具实现')
    expect(wrapper.text()).not.toContain('调用 tool_search')
  })

  it('renders the v2 deferred catalog no-match result', async () => {
    const noMatchAudit: CodingRunAuditSummary = {
      ...audit,
      steps: [{
        tool: 'tool_search',
        status: 'completed',
        action_summary: '调用 tool_search',
        result_summary: '执行完成',
        duration_ms: 12,
        arguments_preview: '{"query":"missing"}',
        result_preview: '{"status":"no_match","query":"missing"}',
        arguments_truncated: false,
        result_truncated: false,
      }],
    }
    const wrapper = mount(CodingRunTrace, {
      props: { runId: 'run-no-match', tools: [], audit: noMatchAudit },
    })

    await wrapper.get('summary').trigger('click')

    expect(wrapper.text()).toContain('无匹配工具')
  })

  it('shows knowledge queries, web queries, constrained domains, and fetched URLs in the audit trail', async () => {
    const retrievalAudit: CodingRunAuditSummary = {
      ...audit,
      tool_count: 3,
      completed_tool_count: 3,
      steps: [
        {
          ...audit.steps[0],
          tool: 'knowledge_search',
          action_summary: '调用 knowledge_search',
          arguments_preview: '{"query":"checkpoint 恢复"}',
        },
        {
          ...audit.steps[0],
          tool: 'search_web',
          action_summary: '调用 search_web',
          arguments_preview: '{"query":"LangGraph checkpoint official documentation","domains":["langchain-ai.github.io"]}',
        },
        {
          ...audit.steps[0],
          tool: 'fetch_web',
          action_summary: '调用 fetch_web',
          arguments_preview: '{"url":"https://langchain-ai.github.io/langgraph/concepts/persistence/"}',
        },
      ],
    }
    const wrapper = mount(CodingRunTrace, {
      props: {
        runId: 'run-retrieval',
        audit: retrievalAudit,
        tools: [
          {
            id: 'tool-knowledge-search',
            tool: 'knowledge_search',
            args: { query: 'checkpoint 恢复' },
            status: 'completed',
            result: 'evidence',
            is_error: false,
          },
          {
            id: 'tool-web-search',
            tool: 'search_web',
            args: {
              query: 'LangGraph checkpoint official documentation',
              domains: ['langchain-ai.github.io'],
            },
            status: 'completed',
            result: 'results',
            is_error: false,
          },
          {
            id: 'tool-web-fetch',
            tool: 'fetch_web',
            args: { url: 'https://langchain-ai.github.io/langgraph/concepts/persistence/' },
            status: 'completed',
            result: 'page content',
            is_error: false,
          },
        ],
      },
    })

    expect(wrapper.get('summary').text()).toContain('搜索知识 checkpoint 恢复')
    expect(wrapper.get('summary').text()).toContain('搜索网页 LangGraph checkpoint official documentation · langchain-ai.github.io')

    await wrapper.get('summary').trigger('click')

    expect(wrapper.text()).toContain('搜索知识 checkpoint 恢复')
    expect(wrapper.text()).toContain('搜索网页 LangGraph checkpoint official documentation · langchain-ai.github.io')
    expect(wrapper.text()).toContain('抓取网页 https://langchain-ai.github.io/langgraph/concepts/persistence/')
  })

  it('merges durable task receipt arguments into a generic run audit step', async () => {
    const taskAudit: CodingRunAuditSummary = {
      ...audit,
      tool_count: 1,
      completed_tool_count: 1,
      steps: [{
        tool: 'task',
        status: 'completed',
        action_summary: '调用 task',
        result_summary: '执行完成',
        duration_ms: 1200,
        arguments_preview: '{}',
        result_preview: 'Task succeeded.',
        arguments_truncated: false,
        result_truncated: false,
      }],
    }
    const wrapper = mount(CodingRunTrace, {
      props: {
        runId: 'run-task',
        audit: taskAudit,
        tools: [{
          id: 'tool-task',
          tool_call_id: 'call-task',
          tool: 'task',
          args: {
            subagent_type: 'practice',
            description: '只读检查 README.md',
            operation_ref: { kind: 'coding_run', id: 'child-1' },
          },
          status: 'completed',
          result: 'Task succeeded.',
          is_error: false,
        }],
      },
    })

    expect(wrapper.get('summary').text()).toContain('Practice 子代理 · 只读检查 README.md')
    await wrapper.get('summary').trigger('click')
    expect(wrapper.get('.trace-step').text()).toContain('Practice 子代理 · 只读检查 README.md')
  })

  it('shows the current action while active and keeps secret-shaped fallback data redacted', async () => {
    const wrapper = mount(CodingRunTrace, {
      props: {
        runId: 'run-live',
        active: true,
        tools: [{
          id: 'tool-1',
          tool: 'run_shell',
          args: {
            command: "curl -H 'Authorization: Bearer live-secret' https://example.com",
            api_key: 'plain-secret',
          },
          status: 'running',
          result: '',
          is_error: false,
        }],
      },
    })

    expect(wrapper.get('summary').text()).toContain('正在执行')
    expect(wrapper.get('summary').text()).not.toContain('live-secret')
    await wrapper.get('summary').trigger('click')
    expect(wrapper.text()).toContain('[REDACTED]')
    expect(wrapper.text()).not.toContain('plain-secret')
    expect(wrapper.text()).not.toContain('live-secret')
    expect(wrapper.find('.step-toggle').exists()).toBe(false)
  })

  it('uses a direct waiting headline for an approval without expanding the panel', () => {
    const wrapper = mount(CodingRunTrace, {
      props: { runId: 'run-waiting', tools: [], active: true, pendingTool: 'run_shell' },
    })

    expect(wrapper.get('summary').text()).toContain('等待确认 · run_shell')
    expect(wrapper.get('details').attributes('open')).toBeUndefined()
  })

  it('does not keep a stale running summary after a completed MCP call', async () => {
    const staleAudit: CodingRunAuditSummary = {
      ...audit,
      steps: [{
        tool: 'scenic_search_scenic_spots',
        status: 'completed',
        action_summary: '调用 scenic_search_scenic_spots',
        result_summary: '执行中',
        duration_ms: 180,
        arguments_preview: '{"city":"杭州"}',
        result_preview: '',
        arguments_truncated: false,
        result_truncated: false,
      }],
    }
    const wrapper = mount(CodingRunTrace, {
      props: { runId: 'run-mcp-stale', tools: [], audit: staleAudit },
    })

    await wrapper.get('summary').trigger('click')

    expect(wrapper.text()).toContain('执行完成')
    expect(wrapper.text()).not.toContain('执行中')
  })

  it('summarizes a completed live tool with no text result as completed', async () => {
    const wrapper = mount(CodingRunTrace, {
      props: {
        runId: 'run-mcp-live',
        tools: [{
          id: 'tool-1',
          tool: 'scenic_search_scenic_spots',
          args: { city: '杭州' },
          status: 'completed',
          result: '',
          is_error: false,
        }],
      },
    })

    await wrapper.get('summary').trigger('click')

    expect(wrapper.text()).toContain('执行完成')
    expect(wrapper.text()).not.toContain('执行中')
  })

  it('renders a recoverable policy rejection as an amber audit step instead of a run failure', async () => {
    const policyAudit: CodingRunAuditSummary = {
      ...audit,
      steps: [
        {
          ...audit.steps[1],
          status: 'error',
          result_summary: '执行失败',
          arguments_preview: '{"command":"sed -n 1,20p src/app.ts"}',
        },
        {
          ...audit.steps[0],
          action_summary: '读取 src/app.ts',
          arguments_preview: '{"path":"src/app.ts"}',
        },
      ],
    }
    const wrapper = mount(CodingRunTrace, {
      props: {
        runId: 'run-policy-recovery',
        audit: policyAudit,
        tools: [
          {
            id: 'tool-policy',
            tool: 'run_shell',
            args: { command: 'sed -n 1,20p src/app.ts' },
            status: 'error',
            result: '',
            is_error: true,
            policy_reason: 'prior_read_required',
          },
          {
            id: 'tool-read',
            tool: 'read_file',
            args: { path: 'src/app.ts' },
            status: 'completed',
            result: 'content',
            is_error: false,
          },
        ],
      },
    })

    expect(wrapper.get('summary').text()).toContain('运行完成')
    await wrapper.get('summary').trigger('click')

    const policyStep = wrapper.findAll('.trace-step')[0]
    expect(policyStep.classes()).toContain('policy-blocked')
    expect(policyStep.classes()).not.toContain('error')
    expect(policyStep.text()).toContain('策略已阻断')
    expect(policyStep.text()).toContain('需先读取目标文件')
  })

  it('distinguishes approval blocking from a real execution failure', async () => {
    const wrapper = mount(CodingRunTrace, {
      props: {
        runId: 'run-block-types',
        active: true,
        tools: [
          {
            id: 'tool-approval',
            tool: 'run_shell',
            args: { command: 'pwd' },
            status: 'blocked',
            result: '',
            is_error: false,
          },
          {
            id: 'tool-error',
            tool: 'run_shell',
            args: { command: 'exit 1' },
            status: 'error',
            result: 'exit_code: 1',
            is_error: true,
          },
        ],
      },
    })

    expect(wrapper.get('summary').text()).toContain('等待确认')
    await wrapper.get('summary').trigger('click')

    const [approvalStep, errorStep] = wrapper.findAll('.trace-step')
    expect(approvalStep.classes()).toContain('approval-blocked')
    expect(approvalStep.text()).toContain('等待确认')
    expect(errorStep.classes()).toContain('error')
    expect(errorStep.text()).toContain('失败')
  })
})
