import { mount } from '@vue/test-utils'
import { expect, it } from 'vitest'
import ContextReceiptChip from './ContextReceiptChip.vue'

const context = {
  surface: 'knowledge' as const,
  workspaceId: 'knowledge-local',
  resource: { type: 'knowledge_page' as const, id: 'page-1', revision: 'page-rev-3', label: 'Agent Harness' },
  selection: { type: 'graph_node' as const, id: 'node-1', revision: 'node-rev-7', label: 'Agent Harness' },
  graphRevision: 'graph-1',
  operationRefs: [],
}

it('labels Knowledge context as pending until the backend confirms a frozen receipt', async () => {
  const wrapper = mount(ContextReceiptChip, { props: { context, removable: true } })

  expect(wrapper.text()).toContain('Agent Harness')
  expect(wrapper.text()).toContain('提交时冻结')
  expect(wrapper.text()).toContain('已绑定图谱')
  expect(wrapper.text()).toContain('已选节点')
  expect(wrapper.text()).not.toContain('node-rev-7')
  await wrapper.get('button[aria-label="移除上下文 Agent Harness"]').trigger('click')
  expect(wrapper.emitted('remove')).toHaveLength(1)
})

it('only says frozen when an explicit receipt state is provided', () => {
  const wrapper = mount(ContextReceiptChip, { props: { context, state: 'frozen' } })
  expect(wrapper.text()).toContain('已冻结')
  expect(wrapper.text()).not.toContain('提交时冻结')
})
