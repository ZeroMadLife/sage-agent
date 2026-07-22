import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ChatToolActivity from './ChatToolActivity.vue'

describe('ChatToolActivity knowledge evidence', () => {
  it('keeps tools collapsed and exposes revision-bound citations on demand', async () => {
    const wrapper = mount(ChatToolActivity, {
      props: {
        isThinking: false,
        tools: [{
          tool: 'knowledge_search',
          args: { query: 'Harness 2.0' },
          status: 'done',
          content: '{"status":"evidence_found"}',
          retrieval: {
            status: 'evidence_found',
            query: 'Harness 2.0',
            usedTokens: 320,
            tokenBudget: 1200,
            omittedCount: 0,
            citations: [{
              citationId: 'kcite_1',
              rank: 1,
              pageRevision: 'krev_page_1',
              sourceRevision: 'krev_source_1',
              sourceKind: 'obsidian',
              sourceRelativePath: 'design/harness.md',
              title: 'Harness 2.0 Design',
              headingPath: ['Retrieval'],
              blockId: 'block_1',
              excerpt: '检索结果必须保留引用和来源版本。',
              truncated: false,
            }],
          },
        }],
      },
    })

    expect(wrapper.text()).toContain('搜索知识 · 1 条证据')
    expect(wrapper.text()).not.toContain('检索结果必须保留引用和来源版本。')

    await wrapper.get('.tool-row').trigger('click')
    expect(wrapper.text()).toContain('Harness 2.0 Design')
    expect(wrapper.text()).toContain('design/harness.md')
    expect(wrapper.text()).not.toContain('检索结果必须保留引用和来源版本。')

    await wrapper.get('button[aria-label="展开引用 Harness 2.0 Design"]').trigger('click')
    expect(wrapper.text()).toContain('检索结果必须保留引用和来源版本。')
    expect(wrapper.text()).toContain('kcite_1')
    expect(wrapper.text()).toContain('页面 krev_page_1')
    expect(wrapper.text()).toContain('来源 krev_source_1')
  })

  it('shows the public subagent profile and task description', () => {
    const wrapper = mount(ChatToolActivity, {
      props: {
        isThinking: false,
        tools: [{
          tool: 'task',
          args: {
            subagent_type: 'practice',
            description: '只读检查 README.md',
            operation_ref: { kind: 'coding_run', id: 'child-1' },
          },
          status: 'done',
          content: 'Task succeeded.',
        }],
      },
    })

    expect(wrapper.text()).toContain('Practice · 只读检查 README.md')
  })

  it('presents an unpromoted deferred tool as a recoverable promotion step', () => {
    const wrapper = mount(ChatToolActivity, {
      props: {
        isThinking: false,
        tools: [{
          tool: 'search_web',
          args: { query: 'LangGraph checkpoint' },
          status: 'error',
          content: "Tool 'search_web' is deferred and has not been promoted. Call tool_search first, then retry with the returned schema.",
        }, {
          tool: 'search_web',
          args: { query: 'LangGraph checkpoint' },
          status: 'done',
          content: '{"results":[]}',
        }],
      },
    })

    expect(wrapper.text()).toContain('1 待提升')
    expect(wrapper.text()).not.toContain('失败')
    expect(wrapper.find('.tool-item.deferred').exists()).toBe(true)
    expect(wrapper.find('.tool-item.error').exists()).toBe(false)
  })

  it('shows the actual Web query and fetched URL in the generic timeline', () => {
    const wrapper = mount(ChatToolActivity, {
      props: {
        isThinking: false,
        tools: [{
          tool: 'search_web',
          args: { query: 'LangGraph checkpoint', domains: ['langchain-ai.github.io'] },
          status: 'done',
          content: '{"status":"evidence_found"}',
        }, {
          tool: 'fetch_web',
          args: { url: 'https://langchain-ai.github.io/langgraph/concepts/persistence/' },
          status: 'done',
          content: '{"status":"evidence_found"}',
        }],
      },
    })

    expect(wrapper.text()).toContain('搜索网页 · LangGraph checkpoint')
    expect(wrapper.text()).toContain('抓取网页 · https://langchain-ai.github.io/langgraph/concepts/persistence/')
  })
})
