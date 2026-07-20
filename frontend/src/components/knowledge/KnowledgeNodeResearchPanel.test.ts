import { mount } from '@vue/test-utils'
import { expect, it } from 'vitest'
import KnowledgeNodeResearchPanel from './KnowledgeNodeResearchPanel.vue'

it('shows factual submission boundaries and emits a research intent', async () => {
  const wrapper = mount(KnowledgeNodeResearchPanel, {
    props: {
      model: {
        nodeId: 'node-1', label: 'Agent Harness', graphRevision: 'graph-revision-long',
        pageRevision: 'page-revision-long', sourceRevision: 'source-revision-long',
        directConnectionCount: 4, goalCapability: '可靠 Harness', evidenceBound: true,
      },
      loading: false,
    },
  })

  expect(wrapper.text()).toContain('研究「Agent Harness」')
  expect(wrapper.text()).toContain('revision 已绑定')
  expect(wrapper.text()).toContain('1 跳 · 4 条连接')
  expect(wrapper.text()).toContain('动作只填入可编辑任务')

  await wrapper.findAll('.research-actions button')[1].trigger('click')
  expect(wrapper.emitted('choose')).toEqual([['evidence']])
})
