import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ChatConversation from './ChatConversation.vue'

describe('ChatConversation', () => {
  it('projects one canonical timeline into a reusable conversation surface', async () => {
    const wrapper = mount(ChatConversation, {
      props: {
        legacyMessages: [],
        messages: [
          { id: 'user-1', run_id: 'run-1', role: 'user', content: '解释当前节点' },
          { id: 'assistant-1', run_id: 'run-1', role: 'assistant', content: '这是可追溯的解释。' },
        ],
        turns: [{
          id: 'turn-1', run_id: 'run-1', user: null, assistant: null,
          tools: [], approvals: [], model: [], context: [], memory: [], agents: [],
          system: [], terminal: null,
        }],
        activeRunId: '',
        selectedRunId: 'run-1',
      },
    })

    expect(wrapper.findAll('.message-turn')).toHaveLength(2)
    expect(wrapper.text()).toContain('解释当前节点')
    expect(wrapper.text()).toContain('这是可追溯的解释。')
    expect(wrapper.get('.timeline-turn').classes()).toContain('selected')
    await wrapper.get('.timeline-turn').trigger('click')
    expect(wrapper.emitted('selectRun')?.[0]).toEqual(['run-1'])
  })
})
