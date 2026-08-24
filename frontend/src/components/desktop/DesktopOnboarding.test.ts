import { mount } from '@vue/test-utils'
import { expect, it } from 'vitest'
import DesktopOnboarding from './DesktopOnboarding.vue'
import type { DesktopOnboardingSnapshot } from '../../desktop/hostAdapter'

function snapshot(overrides: Partial<DesktopOnboardingSnapshot> = {}): DesktopOnboardingSnapshot {
  return {
    status: 'blocked',
    reason_code: 'onboarding_mode_required',
    action: 'choose_onboarding_mode',
    stage: 'choose_mode',
    mode: null,
    workspace_name: null,
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

it('exposes probe, default model, rotation, disconnect and delete for an existing Provider', async () => {
  const wrapper = mount(DesktopOnboarding, {
    props: {
      snapshot: snapshot({
        stage: 'complete', status: 'degraded', mode: 'local', workspace_name: 'workspace',
        providers: [{
          provider_id: 'provider-1', name: 'Provider', base_url: 'https://api.openai.com/v1',
          key_ref: 'keychain://service/provider-1', key_hint: '****test', key_configured: true,
          status: 'connected', reason_code: null, models: ['model-small', 'model-large'],
          default_model: 'model-small',
        }],
      }),
      busy: false,
      error: null,
    },
  })

  await wrapper.get('button[aria-label="探测 Provider"]').trigger('click')
  await wrapper.get('select[aria-label="默认模型"]').setValue('model-large')
  await wrapper.get('button[aria-label="轮换 API Key"]').trigger('click')
  await wrapper.get('input[aria-label="新 API Key"]').setValue('write-only-rotated')
  await wrapper.get('button[data-action="confirm-rotate"]').trigger('click')
  await wrapper.get('button[aria-label="注销 Provider 凭据"]').trigger('click')
  await wrapper.get('button[aria-label="删除 Provider"]').trigger('click')
  await wrapper.get('button[data-action="confirm-delete"]').trigger('click')

  expect(wrapper.emitted('action')).toEqual([
    [{ kind: 'probe_provider', provider_id: 'provider-1' }],
    [{ kind: 'set_default_model', provider_id: 'provider-1', model_id: 'model-large' }],
    [{ kind: 'rotate_provider_key', provider_id: 'provider-1', api_key: 'write-only-rotated' }],
    [{ kind: 'disconnect_provider', provider_id: 'provider-1' }],
    [{ kind: 'delete_provider', provider_id: 'provider-1' }],
  ])
  expect(wrapper.html()).not.toContain('write-only-rotated')
})
