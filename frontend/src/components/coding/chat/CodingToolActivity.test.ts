import { mount } from '@vue/test-utils'
import { expect, it } from 'vitest'
import CodingToolActivity from './CodingToolActivity.vue'

it('truncates long tool results and can expand them', async () => {
  const longResult = `first sentence. ${'x'.repeat(900)}\n+added\n-removed`
  const wrapper = mount(CodingToolActivity, {
    props: {
      isThinking: true,
      tools: [
        {
          tool: 'patch_file',
          args: { path: 'README.md' },
          status: 'done',
          content: longResult,
        },
      ],
    },
  })

  expect(wrapper.find('.tool-list').exists()).toBe(false)

  await wrapper.find('.activity-header').trigger('click')

  expect(wrapper.text()).toContain('展开输出')
  expect(wrapper.text()).not.toContain('removed')

  await wrapper.find('button.show-more').trigger('click')

  expect(wrapper.text()).toContain('收起输出')
  expect(wrapper.text()).toContain('removed')
  expect(wrapper.find('.diff-add').exists()).toBe(true)
  expect(wrapper.find('.diff-remove').exists()).toBe(true)
})

it('renders human readable tool action summaries with collapsed raw arguments', async () => {
  const wrapper = mount(CodingToolActivity, {
    props: {
      isThinking: true,
      tools: [
        {
          tool: 'read_file',
          args: { path: 'README.md' },
          status: 'done',
          content: '',
        },
        {
          tool: 'run_shell',
          args: { command: 'pytest -q' },
          status: 'running',
          content: '',
        },
        {
          tool: 'patch_file',
          args: { path: 'src/app.py', old_text: 'a', new_text: 'b' },
          status: 'done',
          content: '',
        },
      ],
    },
  })

  expect(wrapper.find('.tool-list').exists()).toBe(false)

  await wrapper.find('.activity-header').trigger('click')

  expect(wrapper.text()).toContain('Read README.md')
  expect(wrapper.text()).toContain('Run pytest -q')
  expect(wrapper.text()).toContain('Patch src/app.py')
  expect(wrapper.findAll('.tool-args-details')).toHaveLength(3)
  expect((wrapper.findAll('.tool-args-details')[2].element as HTMLDetailsElement).open).toBe(false)
})

it('keeps full tool arguments available in a collapsed detail disclosure', async () => {
  const wrapper = mount(CodingToolActivity, {
    props: {
      isThinking: false,
      tools: [{
        tool: 'patch_file',
        args: { path: 'src/app.py', old_text: 'before', new_text: 'after' },
        status: 'done',
        content: '',
      }],
    },
  })

  await wrapper.find('.activity-header').trigger('click')

  const details = wrapper.get('.tool-args-details')
  expect((details.element as HTMLDetailsElement).open).toBe(false)
  expect(details.text()).toContain('"old_text": "before"')
  expect(details.text()).toContain('"new_text": "after"')
})

it('uses semantic theme tokens for running, completed, and failed tool states', async () => {
  const wrapper = mount(CodingToolActivity, {
    props: {
      isThinking: true,
      tools: [
        { tool: 'read_file', args: {}, status: 'running', content: '' },
        { tool: 'read_file', args: {}, status: 'done', content: '' },
        { tool: 'read_file', args: {}, status: 'error', content: '' },
      ],
    },
  })

  await wrapper.find('.activity-header').trigger('click')

  expect(wrapper.html()).toContain('var(--sage-warning)')
  expect(wrapper.html()).toContain('var(--sage-success)')
  expect(wrapper.html()).toContain('var(--sage-danger)')
  expect(wrapper.find('.tool-spinner').attributes('style')).toContain('--sage-warning')
})
