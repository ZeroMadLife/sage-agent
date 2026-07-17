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
})
