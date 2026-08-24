import { mount } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
import DesktopHostGate from './DesktopHostGate.vue'

const { desktopHostStatus, desktopCapabilities, desktopOnboardingStatus, desktopOnboardingAction, desktopExit, desktopOpenDiagnostics, onDesktopConnectionState } = vi.hoisted(() => ({
  desktopHostStatus: vi.fn(),
  desktopCapabilities: vi.fn(),
  desktopOnboardingStatus: vi.fn(),
  desktopOnboardingAction: vi.fn(),
  desktopExit: vi.fn(),
  desktopOpenDiagnostics: vi.fn(),
  onDesktopConnectionState: vi.fn((_listener: (state: 'ready' | 'degraded') => void) => () => undefined),
}))
let connectionListener: ((state: 'ready' | 'degraded') => void) | undefined
vi.mock('../../desktop/hostAdapter', () => ({
  desktopHostStatus,
  desktopCapabilities,
  desktopOnboardingStatus,
  desktopOnboardingAction,
  desktopExit,
  desktopOpenDiagnostics,
  onDesktopConnectionState,
}))

beforeEach(() => {
  vi.useFakeTimers()
  desktopHostStatus.mockReset()
  desktopCapabilities.mockReset()
  desktopOnboardingStatus.mockReset()
  desktopOnboardingAction.mockReset()
  desktopExit.mockReset()
  desktopOpenDiagnostics.mockReset()
  onDesktopConnectionState.mockReset()
  onDesktopConnectionState.mockImplementation((listener) => {
    connectionListener = listener
    return () => {
      if (connectionListener === listener) connectionListener = undefined
    }
  })
  desktopOnboardingStatus.mockResolvedValue({
    status: 'degraded', reason_code: 'optional_capabilities_unavailable', action: 'review_capabilities',
    stage: 'complete', mode: 'local', workspace_name: 'workspace', providers: [],
    capabilities: {
      provider: { status: 'blocked', reason_code: 'provider_not_configured', action: 'configure_provider' },
    },
  })
})

it('renders the rebuildable first-launch flow before the capability surface', async () => {
  desktopHostStatus.mockResolvedValue({
    state: 'ready', reasonCode: null, action: null,
    session: { endpoint: 'http://127.0.0.1:49152', bearer: 'hidden', instanceId: 'i' },
  })
  desktopCapabilities.mockResolvedValue({
    status: 'degraded', api_version: '1', build_sha: 'build',
    capabilities: {
      api: { status: 'ready', reason_code: null, action: null },
      conversation: { status: 'ready', reason_code: null, action: null },
      rag: { status: 'ready', reason_code: null, action: null },
    },
  })
  desktopOnboardingStatus.mockResolvedValue({
    status: 'blocked', reason_code: 'onboarding_mode_required', action: 'choose_onboarding_mode',
    stage: 'choose_mode', mode: null, workspace_name: null, providers: [], capabilities: {},
  })
  desktopOnboardingAction.mockResolvedValue({
    status: 'blocked', reason_code: 'workspace_required', action: 'select_workspace',
    stage: 'select_workspace', mode: 'local', workspace_name: null, providers: [], capabilities: {},
  })

  const wrapper = mount(DesktopHostGate)
  await vi.advanceTimersByTimeAsync(0)

  expect(wrapper.text()).toContain('完成首次设置')
  await wrapper.get('button[data-mode="local"]').trigger('click')
  await vi.advanceTimersByTimeAsync(0)
  expect(desktopOnboardingAction).toHaveBeenCalledWith({ kind: 'choose_mode', mode: 'local' })
  expect(wrapper.find('input[aria-label="Workspace 路径"]').exists()).toBe(true)
})

it('shows a quiet capability surface with browser-safe diagnostics', async () => {
  desktopHostStatus.mockResolvedValue({
    state: 'ready', reasonCode: null, action: null,
    session: { endpoint: 'http://127.0.0.1:49152', bearer: 'never-render', instanceId: 'i' },
  })
  desktopCapabilities.mockResolvedValue({
    status: 'degraded', api_version: '1', build_sha: 'build',
    capabilities: {
      api: { status: 'ready', reason_code: null, action: null },
      provider: { status: 'blocked', reason_code: 'provider_not_configured', action: 'configure_provider' },
    },
  })

  const wrapper = mount(DesktopHostGate)
  await vi.advanceTimersByTimeAsync(0)

  expect(wrapper.text()).toContain('Sage 部分能力受限')
  expect(wrapper.text()).toContain('Provider')
  expect(wrapper.text()).toContain('provider_not_configured')
  expect(wrapper.text()).toContain('configure_provider')
  expect(wrapper.text()).not.toContain('never-render')
  expect(wrapper.find('[data-capability="api"]').attributes('data-status')).toBe('ready')
  expect(wrapper.find('[data-capability="provider"]').attributes('data-status')).toBe('blocked')
  expect(wrapper.attributes('data-host-state')).toBe('degraded')

  await vi.advanceTimersByTimeAsync(2000)
  expect(desktopHostStatus).toHaveBeenCalledTimes(2)
})

it('opens the real workbench only when conversation and SQLite RAG are ready', async () => {
  desktopHostStatus.mockResolvedValue({
    state: 'ready', reasonCode: null, action: null,
    session: { endpoint: 'http://127.0.0.1:49152', bearer: 'hidden', instanceId: 'i' },
  })
  desktopCapabilities.mockResolvedValue({
    status: 'degraded', api_version: '1', build_sha: 'build',
    capabilities: {
      api: { status: 'ready', reason_code: null, action: null },
      conversation: { status: 'ready', reason_code: null, action: null },
      rag: { status: 'ready', reason_code: null, action: null },
    },
  })
  desktopOnboardingStatus.mockResolvedValue({
    status: 'degraded', reason_code: 'optional_capabilities_unavailable', action: 'review_capabilities',
    stage: 'complete', mode: 'local', workspace_name: 'workspace', providers: [],
    capabilities: {
      conversation: { status: 'ready', reason_code: null, action: null },
      rag: { status: 'ready', reason_code: null, action: null },
      side_effect_tools: { status: 'blocked', reason_code: 'docker_not_available', action: 'continue_without_side_effect_tools' },
    },
  })

  const wrapper = mount(DesktopHostGate)
  await vi.advanceTimersByTimeAsync(0)

  expect(wrapper.emitted('availability')?.at(-1)).toEqual([true])
})

it('keeps a quiet Provider management entry after the workbench opens', async () => {
  desktopHostStatus.mockResolvedValue({
    state: 'ready', reasonCode: null, action: null,
    session: { endpoint: 'http://127.0.0.1:49152', bearer: 'hidden', instanceId: 'i' },
  })
  desktopCapabilities.mockResolvedValue({
    status: 'ready', api_version: '1', build_sha: 'build',
    capabilities: {
      conversation: { status: 'ready', reason_code: null, action: null },
      rag: { status: 'ready', reason_code: null, action: null },
    },
  })
  desktopOnboardingStatus.mockResolvedValue({
    status: 'ready', reason_code: null, action: null,
    stage: 'complete', mode: 'local', workspace_name: 'workspace',
    providers: [{
      provider_id: 'provider-1', name: 'Local Provider', base_url: 'http://127.0.0.1:11434/v1',
      key_ref: 'keychain:provider-1', key_hint: '...test', key_configured: true,
      status: 'ready', reason_code: null, models: ['local-model'], default_model: 'local-model',
    }],
    capabilities: {
      conversation: { status: 'ready', reason_code: null, action: null },
      rag: { status: 'ready', reason_code: null, action: null },
    },
  })

  const wrapper = mount(DesktopHostGate)
  await vi.advanceTimersByTimeAsync(0)

  expect(wrapper.find('[data-action="open-provider-management"]').exists()).toBe(true)
  expect(wrapper.find('[role="dialog"]').exists()).toBe(false)

  await wrapper.get('[data-action="open-provider-management"]').trigger('click')
  expect(wrapper.get('[role="dialog"]').attributes('aria-label')).toBe('本地运行状态与 Provider')
  expect(wrapper.text()).toContain('Local Provider')

  await wrapper.get('[data-action="close-provider-management"]').trigger('click')
  expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
})

it('automatically takes over the window when the desktop connection is lost', async () => {
  desktopHostStatus.mockResolvedValue({
    state: 'ready', reasonCode: null, action: null,
    session: { endpoint: 'http://127.0.0.1:49152', bearer: 'hidden', instanceId: 'i' },
  })
  desktopCapabilities.mockResolvedValue({
    status: 'ready', api_version: '1', build_sha: 'build',
    capabilities: {
      conversation: { status: 'ready', reason_code: null, action: null },
      rag: { status: 'ready', reason_code: null, action: null },
    },
  })
  desktopOnboardingStatus.mockResolvedValue({
    status: 'ready', reason_code: null, action: null,
    stage: 'complete', mode: 'local', workspace_name: 'workspace', providers: [],
    capabilities: {
      conversation: { status: 'ready', reason_code: null, action: null },
      rag: { status: 'ready', reason_code: null, action: null },
    },
  })

  const wrapper = mount(DesktopHostGate)
  await vi.advanceTimersByTimeAsync(0)
  expect(wrapper.find('[role="dialog"]').exists()).toBe(false)

  connectionListener?.('degraded')
  await wrapper.vm.$nextTick()

  expect(wrapper.emitted('availability')?.at(-1)).toEqual([false])
  expect(wrapper.get('[role="dialog"]').attributes('aria-modal')).toBe('true')
  expect(wrapper.text()).toContain('Sage 正在恢复')
  expect(wrapper.find('[data-action="close-provider-management"]').exists()).toBe(false)
})

it('polls recoverable crashes and exposes an explicit exit command', async () => {
  desktopHostStatus
    .mockResolvedValueOnce({ state: 'degraded', reasonCode: 'desktop_sidecar_crashed', action: 'wait_for_restart', session: null })
    .mockResolvedValueOnce({ state: 'blocked', reasonCode: 'desktop_crash_budget_exhausted', action: 'open_diagnostics', session: null })
  const wrapper = mount(DesktopHostGate)
  await vi.advanceTimersByTimeAsync(500)

  expect(desktopHostStatus).toHaveBeenCalledTimes(2)
  expect(wrapper.text()).toContain('desktop_crash_budget_exhausted')
  expect(wrapper.find('[data-action="diagnostics"]').exists()).toBe(true)
  await wrapper.get('[data-action="diagnostics"]').trigger('click')
  expect(desktopOpenDiagnostics).toHaveBeenCalledOnce()
  await wrapper.get('[data-action="exit"]').trigger('click')
  expect(desktopExit).toHaveBeenCalledOnce()
})

it('moves a transient capability disconnect to degraded and keeps polling', async () => {
  desktopHostStatus.mockResolvedValue({
    state: 'ready', reasonCode: null, action: null,
    session: { endpoint: 'http://127.0.0.1:49152', bearer: 'hidden', instanceId: 'i' },
  })
  desktopCapabilities
    .mockRejectedValueOnce(new Error('desktop_session_unavailable'))
    .mockResolvedValueOnce({
      status: 'ready', api_version: '1', build_sha: 'build',
      capabilities: { api: { status: 'ready', reason_code: null, action: null } },
    })
  desktopOnboardingStatus.mockResolvedValue({
    status: 'ready', reason_code: null, action: null,
    stage: 'complete', mode: 'local', workspace_name: 'workspace', providers: [],
    capabilities: { provider: { status: 'ready', reason_code: null, action: null } },
  })

  const wrapper = mount(DesktopHostGate)
  await vi.advanceTimersByTimeAsync(0)
  expect(wrapper.attributes('data-host-state')).toBe('degraded')
  expect(wrapper.text()).toContain('desktop_connection_lost')

  await vi.advanceTimersByTimeAsync(500)
  expect(wrapper.attributes('data-host-state')).toBe('ready')
  expect(desktopCapabilities).toHaveBeenCalledTimes(2)
})
