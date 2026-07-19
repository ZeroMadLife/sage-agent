import { mount } from '@vue/test-utils'
import { expect, it } from 'vitest'
import CodingApprovalCard from './CodingApprovalCard.vue'

it('renders approval details and emits supported approval choices', async () => {
  const wrapper = mount(CodingApprovalCard, {
    props: {
      approval: {
        approval_id: 'appr_1',
        session_id: 'c1',
        tool: 'write_file',
        args: { path: 'README.md' },
        description: 'write_file requires approval.',
        pattern_key: 'tool:write_file',
      },
    },
  })

  expect(wrapper.text()).toContain('write_file')
  expect(wrapper.text()).toContain('写入文件前需要确认。')
  expect(wrapper.text()).not.toContain('requires approval')

  await wrapper.find('button.allow').trigger('click')
  await wrapper.find('button.session').trigger('click')
  await wrapper.find('button.deny').trigger('click')

  expect(wrapper.emitted('respond')).toEqual([['once'], ['session'], ['deny']])
})

it('renders a localized shell approval with a distinct primary action', () => {
  const wrapper = mount(CodingApprovalCard, {
    props: {
      approval: {
        approval_id: 'appr_shell',
        session_id: 'c1',
        tool: 'run_shell',
        args: { command: 'pwd' },
        description: 'run_shell requires approval.',
        pattern_key: 'tool:run_shell',
      },
    },
  })

  expect(wrapper.text()).toContain('执行 Shell 命令前需要确认。')
  expect(wrapper.text()).not.toContain('requires approval')
  expect(wrapper.get('button.allow').text()).toBe('允许一次')
})

it('redacts secret-shaped values from the shell command summary', () => {
  const wrapper = mount(CodingApprovalCard, {
    props: {
      approval: {
        approval_id: 'appr_secret',
        session_id: 'c1',
        tool: 'run_shell',
        args: {
          command: 'OPENAI_API_KEY=plain-secret curl -H "Authorization: Bearer bearer-secret" example.test',
        },
        description: 'run_shell requires approval.',
        pattern_key: 'tool:run_shell',
      },
    },
  })

  expect(wrapper.text()).not.toContain('plain-secret')
  expect(wrapper.text()).not.toContain('bearer-secret')
  expect(wrapper.text()).toContain('OPENAI_API_KEY=[REDACTED]')
  expect(wrapper.text()).toContain('Authorization: [REDACTED]')
})

it('renders diff preview when provided', () => {
  const wrapper = mount(CodingApprovalCard, {
    props: {
      approval: {
        approval_id: 'appr_1',
        session_id: 'c1',
        tool: 'patch_file',
        args: { path: 'README.md' },
        description: 'patch_file requires approval.',
        pattern_key: 'tool:patch_file',
        diff_preview: [
          { type: 'remove', text: 'old title' },
          { type: 'add', text: 'new title' },
        ],
      },
    },
  })

  expect(wrapper.text()).toContain('old title')
  expect(wrapper.text()).toContain('new title')
  expect(wrapper.find('.diff-remove').exists()).toBe(true)
  expect(wrapper.find('.diff-add').exists()).toBe(true)
})

it('requires a one-time decision for every knowledge learning deposit', () => {
  const wrapper = mount(CodingApprovalCard, {
    props: {
      approval: {
        approval_id: 'appr_knowledge',
        session_id: 'c1',
        tool: 'knowledge_learn',
        args: { topic: 'Harness 2.0', citation_ids: ['kcite_1', 'kcite_2'] },
        description: '保存本轮引用证据到知识库前需要确认。',
        pattern_key: 'tool:knowledge_learn',
      },
    },
  })

  expect(wrapper.text()).toContain('将“Harness 2.0”与 2 条引用证据保存到知识库')
  expect(wrapper.find('button.session').exists()).toBe(false)
  expect(wrapper.find('button.allow').exists()).toBe(true)
  expect(wrapper.find('button.deny').exists()).toBe(true)
})

it('requires a one-time decision before writing durable memory', () => {
  const wrapper = mount(CodingApprovalCard, {
    props: {
      approval: {
        approval_id: 'appr_memory',
        session_id: 'c1',
        tool: 'remember',
        args: { topic: 'project-conventions', fact: 'Keep revision-bound citations.' },
        description: '保存事实到长期工作区记忆前需要确认。',
        pattern_key: 'tool:remember',
      },
    },
  })

  expect(wrapper.text()).toContain(
    '保存到 project-conventions: Keep revision-bound citations.',
  )
  expect(wrapper.find('button.session').exists()).toBe(false)
  expect(wrapper.find('button.allow').exists()).toBe(true)
})

it('summarizes write content instead of rendering the full payload', () => {
  const content = 'def two_sum():\n    return [0, 1]\n'
  const wrapper = mount(CodingApprovalCard, {
    props: {
      approval: {
        approval_id: 'appr_1',
        session_id: 'c1',
        tool: 'write_file',
        args: { path: 'two_sum.py', content },
        description: 'write_file requires approval.',
        pattern_key: 'tool:write_file',
      },
    },
  })

  expect(wrapper.text()).toContain('two_sum.py')
  expect(wrapper.text()).toContain('将写入 2 行')
  expect(wrapper.text()).not.toContain(content)
})

it('opens a full diff modal for approval review', async () => {
  const wrapper = mount(CodingApprovalCard, {
    props: {
      approval: {
        approval_id: 'appr_1',
        session_id: 'c1',
        tool: 'write_file',
        args: { path: 'src/main.py' },
        description: 'write_file requires approval.',
        pattern_key: 'tool:write_file',
        diff_preview: [
          { type: 'remove', text: 'print("old")' },
          { type: 'add', text: 'print("new")' },
        ],
      },
    },
  })

  expect(wrapper.find('[role="dialog"]').exists()).toBe(false)

  await wrapper.find('button.view-diff').trigger('click')

  const dialog = wrapper.find('[role="dialog"]')
  expect(dialog.exists()).toBe(true)
  expect(dialog.text()).toContain('src/main.py')
  expect(dialog.text()).toContain('print("old")')
  expect(dialog.text()).toContain('print("new")')

  await wrapper.find('button.close-diff').trigger('click')

  expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
})
