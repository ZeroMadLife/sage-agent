import { mount } from '@vue/test-utils'
import { expect, it, vi } from 'vitest'
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

  expect(wrapper.get('.tool-row').attributes('aria-expanded')).toBe('false')
  await wrapper.get('.tool-row').trigger('click')

  expect(wrapper.text()).toContain('展开完整输出')
  expect(wrapper.text()).not.toContain('removed')

  await wrapper.find('button.show-more').trigger('click')

  expect(wrapper.text()).toContain('收起输出')
  expect(wrapper.text()).toContain('removed')
  expect(wrapper.find('.result-panel').exists()).toBe(true)
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

  expect(wrapper.text()).toContain('读取 README.md')
  expect(wrapper.text()).toContain('pytest -q')
  expect(wrapper.text()).toContain('修改 src/app.py')
  expect(wrapper.findAll('.tool-row')[0].attributes('aria-expanded')).toBe('false')
  expect(wrapper.findAll('.tool-row')[1].attributes('aria-expanded')).toBe('true')
  expect(wrapper.findAll('.code-panel')).toHaveLength(1)
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

  await wrapper.get('.tool-row').trigger('click')

  expect(wrapper.get('.code-panel').text()).toContain('"old_text": "before"')
  expect(wrapper.get('.code-panel').text()).toContain('"new_text": "after"')
  expect(wrapper.get('.code-panel').html()).toContain('hljs-attr')
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

  expect(wrapper.findAll('.tool-item')[0].classes()).toContain('running')
  expect(wrapper.findAll('.tool-item')[1].classes()).toContain('done')
  expect(wrapper.findAll('.tool-item')[2].classes()).toContain('error')
  expect(wrapper.findAll('.tool-spinner')).toHaveLength(1)
})

it('copies the original parameter and result payloads from icon controls', async () => {
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText },
  })
  const wrapper = mount(CodingToolActivity, {
    props: {
      isThinking: true,
      tools: [{
        tool: 'search',
        args: { pattern: '*.py', limit: 3 },
        status: 'running',
        content: '{"total": 3}',
      }],
    },
  })

  await wrapper.get('button[aria-label="复制参数"]').trigger('click')
  await wrapper.get('button[aria-label="复制结果"]').trigger('click')

  expect(writeText).toHaveBeenNthCalledWith(1, JSON.stringify({ pattern: '*.py', limit: 3 }, null, 2))
  expect(writeText).toHaveBeenNthCalledWith(2, '{"total": 3}')
  expect(wrapper.get('.result-panel').text()).toContain('结果 · JSON')
})
