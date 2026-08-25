import { mount } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
import DesktopOnboarding from './DesktopOnboarding.vue'
import type { DesktopOnboardingSnapshot } from '../../desktop/hostAdapter'

const { open } = vi.hoisted(() => ({ open: vi.fn() }))
vi.mock('@tauri-apps/plugin-dialog', () => ({ open }))

beforeEach(() => {
  open.mockReset()
})

function snapshot(overrides: Partial<DesktopOnboardingSnapshot> = {}): DesktopOnboardingSnapshot {
  return {
    status: 'blocked',
    reason_code: 'onboarding_mode_required',
    action: 'choose_onboarding_mode',
    stage: 'choose_mode',
    mode: null,
    workspace_name: null,
    active_provider_id: null,
    providers: [],
    capabilities: {},
    ...overrides,
  }
}

it('offers explicit Local and Cloud choices without pretending Cloud OAuth exists', async () => {
  const wrapper = mount(DesktopOnboarding, { props: { snapshot: snapshot(), busy: false, error: null } })

  await wrapper.get('button[data-mode="local"]').trigger('click')
  await wrapper.get('button[data-mode="cloud"]').trigger('click')

  expect(wrapper.emitted('action')).toEqual([
    [{ kind: 'choose_mode', mode: 'local' }],
    [{ kind: 'choose_mode', mode: 'cloud' }],
  ])
})

it('clears the write-only Provider key immediately after submitting it', async () => {
  const wrapper = mount(DesktopOnboarding, {
    props: {
      snapshot: snapshot({
        stage: 'configure_provider', mode: 'local', workspace_name: 'workspace',
        reason_code: 'provider_not_configured', action: 'configure_provider',
      }),
      busy: false,
      error: null,
    },
  })

  await wrapper.get('input[aria-label="Provider 名称"]').setValue('Local OpenAI')
  await wrapper.get('input[aria-label="Base URL"]').setValue('https://api.openai.com/v1')
  await wrapper.get('input[aria-label="API Key"]').setValue('write-only-component-secret')
  await wrapper.get('input[aria-label="默认模型"]').setValue('gpt-test')
  await wrapper.get('button[data-action="save-provider"]').trigger('click')

  expect(wrapper.emitted('action')?.[0]).toEqual([{
    kind: 'add_provider',
    name: 'Local OpenAI',
    base_url: 'https://api.openai.com/v1',
    api_key: 'write-only-component-secret',
    default_model: 'gpt-test',
  }])
  expect((wrapper.get('input[aria-label="API Key"]').element as HTMLInputElement).value).toBe('')
  expect(wrapper.html()).not.toContain('write-only-component-secret')
})

it('uses the native directory picker and submits its selected workspace', async () => {
  open.mockResolvedValue('/tmp/sage-learning-space')
  const wrapper = mount(DesktopOnboarding, {
    props: {
      snapshot: snapshot({
        stage: 'select_workspace', mode: 'local',
        reason_code: 'workspace_required', action: 'select_workspace',
      }),
      busy: false,
      error: null,
    },
  })

  await wrapper.get('button[data-action="choose-workspace"]').trigger('click')
  expect(open).toHaveBeenCalledWith({
    directory: true,
    multiple: false,
    title: '选择 Sage 学习空间',
    canCreateDirectories: false,
  })
  expect((wrapper.get('input[aria-label="Workspace 路径"]').element as HTMLInputElement).value)
    .toBe('/tmp/sage-learning-space')

  await wrapper.get('form').trigger('submit')
  expect(wrapper.emitted('action')).toEqual([[
    { kind: 'select_workspace', workspace_path: '/tmp/sage-learning-space' },
  ]])
})

it('keeps picker cancellation and failures recoverable without exposing raw errors', async () => {
  open.mockResolvedValueOnce(null)
  const wrapper = mount(DesktopOnboarding, {
    props: {
      snapshot: snapshot({ stage: 'select_workspace', mode: 'local', reason_code: 'select_workspace', action: 'select_workspace' }),
      busy: false,
      error: null,
    },
  })

  await wrapper.get('button[data-action="choose-workspace"]').trigger('click')
  expect(wrapper.emitted('action')).toBeUndefined()

  open.mockRejectedValueOnce(new Error('native dialog internals'))
  await wrapper.get('button[data-action="choose-workspace"]').trigger('click')
  expect(wrapper.text()).toContain('文件夹选择器没有打开成功')
  expect(wrapper.text()).not.toContain('native dialog internals')
  expect(wrapper.text()).not.toContain('select_workspace')
})

it('maps invalid manual workspace paths to actionable Chinese messages', () => {
  const wrapper = mount(DesktopOnboarding, {
    props: {
      snapshot: snapshot({ stage: 'select_workspace', mode: 'local', reason_code: 'workspace_unavailable', action: 'select_workspace' }),
      busy: false,
      error: null,
    },
  })

  expect(wrapper.text()).toContain('这个目录暂时无法使用')
  expect(wrapper.text()).toContain('请确认路径存在且可以访问')
  expect(wrapper.text()).not.toContain('workspace_unavailable')
})

it('exposes probe, default model, rotation, disconnect and delete for an existing Provider', async () => {
  const wrapper = mount(DesktopOnboarding, {
    props: {
      snapshot: snapshot({
        stage: 'complete', status: 'degraded', mode: 'local', workspace_name: 'workspace',
        providers: [{
          provider_id: 'provider-1', name: 'Provider', base_url: 'https://api.openai.com/v1',
          key_ref: 'keychain://service/provider-1', key_hint: '****test', key_configured: true,
          status: 'connected', reason_code: null, models: ['model-small', 'model-large'],
          default_model: 'model-small', is_active: false,
        }],
      }),
      busy: false,
      error: null,
    },
  })

  await wrapper.get('button[aria-label="探测 Provider"]').trigger('click')
  await wrapper.get('button[aria-label="设为当前 Provider"]').trigger('click')
  await wrapper.get('select[aria-label="默认模型"]').setValue('model-large')
  await wrapper.get('button[aria-label="轮换 API Key"]').trigger('click')
  await wrapper.get('input[aria-label="新 API Key"]').setValue('write-only-rotated')
  await wrapper.get('button[data-action="confirm-rotate"]').trigger('click')
  await wrapper.get('button[aria-label="注销 Provider 凭据"]').trigger('click')
  await wrapper.get('button[aria-label="删除 Provider"]').trigger('click')
  await wrapper.get('button[data-action="confirm-delete"]').trigger('click')

  expect(wrapper.emitted('action')).toEqual([
    [{ kind: 'probe_provider', provider_id: 'provider-1' }],
    [{ kind: 'set_active_provider', provider_id: 'provider-1' }],
    [{ kind: 'set_default_model', provider_id: 'provider-1', model_id: 'model-large' }],
    [{ kind: 'rotate_provider_key', provider_id: 'provider-1', api_key: 'write-only-rotated' }],
    [{ kind: 'disconnect_provider', provider_id: 'provider-1' }],
    [{ kind: 'delete_provider', provider_id: 'provider-1' }],
  ])
  expect(wrapper.html()).not.toContain('write-only-rotated')
})
