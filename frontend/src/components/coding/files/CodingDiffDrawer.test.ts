import { mount } from '@vue/test-utils'
import { expect, it } from 'vitest'
import CodingDiffDrawer from './CodingDiffDrawer.vue'
import type { CodingRunDiff } from '../../../types/api'

const sampleDiff: CodingRunDiff = {
  run_id: 'run_1',
  file_count: 3,
  truncated: false,
  changed_files: [
    {
      path: 'README.md',
      status: 'modified',
      before_hash: 'aaa',
      after_hash: 'bbb',
      diff: '--- README.md\n+++ README.md\n@@ -1 +1 @@\n-old\n+new',
      truncated: false,
      binary: false,
      ignored_sensitive: false,
    },
    {
      path: 'src/new.ts',
      status: 'added',
      before_hash: '',
      after_hash: 'ccc',
      diff: '+export const x = 1',
      truncated: false,
      binary: false,
      ignored_sensitive: false,
    },
    {
      path: 'old.txt',
      status: 'deleted',
      before_hash: 'ddd',
      after_hash: '',
      diff: '-gone',
      truncated: false,
      binary: false,
      ignored_sensitive: false,
    },
  ],
}

it('renders the file list with status badges when visible', () => {
  const wrapper = mount(CodingDiffDrawer, {
    props: { diff: sampleDiff, visible: true },
  })

  expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
  expect(wrapper.text()).toContain('运行变更 (3 个文件)')
  expect(wrapper.findAll('.file-item')).toHaveLength(3)
  expect(wrapper.find('.file-path').text()).toContain('README.md')
  expect(wrapper.find('.file-status.status-modified').text()).toBe('修改')
  expect(wrapper.find('.file-status.status-added').text()).toBe('新增')
  expect(wrapper.find('.file-status.status-deleted').text()).toBe('删除')
})

it('expands the diff content when a file is clicked', async () => {
  const wrapper = mount(CodingDiffDrawer, {
    props: { diff: sampleDiff, visible: true },
  })

  expect(wrapper.find('.diff-content').exists()).toBe(false)

  await wrapper.find('.file-header').trigger('click')

  expect(wrapper.find('.diff-content').exists()).toBe(true)
  expect(wrapper.find('.diff-content').text()).toContain('-old')
  expect(wrapper.find('.diff-content').text()).toContain('+new')

  // Collapses again on a second click.
  await wrapper.find('.file-header').trigger('click')
  expect(wrapper.find('.diff-content').exists()).toBe(false)
})

it('switches an expanded textual diff between unified and side-by-side views', async () => {
  const wrapper = mount(CodingDiffDrawer, {
    props: { diff: sampleDiff, visible: true },
  })

  await wrapper.find('.file-header').trigger('click')
  expect(wrapper.get('[aria-label="Diff 视图"]').text()).toContain('统一')

  await wrapper.get('button[aria-label="切换为并排 Diff"]').trigger('click')
  expect(wrapper.find('.side-by-side-diff').exists()).toBe(true)
  expect(wrapper.text()).toContain('并排')
  expect(wrapper.findAll('.diff-line-number').length).toBeGreaterThan(0)
})

it('shows a truncated note when the diff is truncated', () => {
  const wrapper = mount(CodingDiffDrawer, {
    props: { diff: { ...sampleDiff, truncated: true }, visible: true },
  })

  expect(wrapper.find('.diff-truncated-note').exists()).toBe(true)
  expect(wrapper.text()).toContain('部分文件未显示')
})

it('does not render when not visible', () => {
  const wrapper = mount(CodingDiffDrawer, {
    props: { diff: sampleDiff, visible: false },
  })

  expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
  expect(wrapper.find('.diff-drawer').exists()).toBe(false)
})

it('shows an empty state when there are no changed files', () => {
  const wrapper = mount(CodingDiffDrawer, {
    props: {
      diff: { run_id: 'run_1', changed_files: [], file_count: 0, truncated: false },
      visible: true,
    },
  })

  expect(wrapper.find('.diff-empty').exists()).toBe(true)
  expect(wrapper.text()).toContain('暂无变更文件')
})
