import { mount } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
import DesktopHostGate from './DesktopHostGate.vue'

const { desktopHostStatus, desktopCapabilities, desktopExit } = vi.hoisted(() => ({
  desktopHostStatus: vi.fn(),
  desktopCapabilities: vi.fn(),
  desktopExit: vi.fn(),
}))
vi.mock('../../desktop/hostAdapter', () => ({
  desktopHostStatus,
  desktopCapabilities,
  desktopExit,
}))

beforeEach(() => {
  vi.useFakeTimers()
  desktopHostStatus.mockReset()
  desktopCapabilities.mockReset()
  desktopExit.mockReset()
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
  await vi.runAllTimersAsync()

  expect(wrapper.text()).toContain('Sage 已启动')
  expect(wrapper.text()).toContain('Provider')
  expect(wrapper.text()).toContain('provider_not_configured')
  expect(wrapper.text()).toContain('configure_provider')
  expect(wrapper.text()).not.toContain('never-render')
  expect(wrapper.find('[data-capability="api"]').attributes('data-status')).toBe('ready')
  expect(wrapper.find('[data-capability="provider"]').attributes('data-status')).toBe('blocked')
})

it('polls recoverable crashes and exposes an explicit exit command', async () => {
  desktopHostStatus
    .mockResolvedValueOnce({ state: 'degraded', reasonCode: 'desktop_sidecar_crashed', action: 'wait_for_restart', session: null })
    .mockResolvedValueOnce({ state: 'blocked', reasonCode: 'desktop_crash_budget_exhausted', action: 'open_diagnostics', session: null })
  const wrapper = mount(DesktopHostGate)
  await vi.runAllTimersAsync()

  expect(desktopHostStatus).toHaveBeenCalledTimes(2)
  expect(wrapper.text()).toContain('desktop_crash_budget_exhausted')
  await wrapper.get('[data-action="exit"]').trigger('click')
  expect(desktopExit).toHaveBeenCalledOnce()
})
