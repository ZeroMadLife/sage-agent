import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import HarnessContextSummary from './HarnessContextSummary.vue'

describe('HarnessContextSummary', () => {
  it('shows revision-bound selection and operation references', () => {
    const wrapper = mount(HarnessContextSummary, {
      props: {
        context: {
          surface: 'knowledge',
          workspaceId: 'knowledge-local',
          resource: {
            type: 'knowledge_page',
            id: 'page-42',
            revision: 'revision-12345678901234567890',
            label: 'Agent Harness',
          },
          selection: {
            type: 'graph_node',
            id: 'node-42',
            revision: 'revision-12345678901234567890',
            label: 'Agent Harness',
          },
          graphRevision: 'graph-3',
          operationRefs: [{ kind: 'knowledge_job', id: 'job-7' }],
        },
        operationLabels: { 'job-7': 'Sage learning / notes' },
      },
    })

    expect(wrapper.text()).toContain('Agent Harness')
    expect(wrapper.text()).toContain('revision-123456789')
    expect(wrapper.text()).toContain('Sage learning / notes')
    expect(wrapper.text()).toContain('job-7')
  })

  it('renders an honest workspace-level state without a fabricated selection', () => {
    const wrapper = mount(HarnessContextSummary, {
      props: {
        context: {
          surface: 'knowledge',
          workspaceId: 'knowledge-local',
          resource: null,
          selection: null,
          operationRefs: [],
        },
      },
    })

    expect(wrapper.text()).toContain('尚未选择知识内容')
    expect(wrapper.find('[aria-label="关联任务"]').exists()).toBe(false)
  })
})
