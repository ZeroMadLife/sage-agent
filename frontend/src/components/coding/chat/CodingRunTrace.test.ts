import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { CodingRunAuditSummary } from '../../../types/api'
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
})
