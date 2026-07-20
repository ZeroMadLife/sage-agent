import { mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, expect, it, vi } from 'vitest'
import CloudAuthGate from './CloudAuthGate.vue'

const loginWithInvite = vi.fn()
const startGitHubLogin = vi.fn()
const options = vi.fn()

vi.mock('../../composables/useCloudAuth', () => ({
  useCloudAuth: () => ({ loginWithInvite, startGitHubLogin, options }),
}))

beforeEach(() => {
  loginWithInvite.mockReset()
  startGitHubLogin.mockReset()
  options.mockReset().mockResolvedValue({ canary_invite_login: true, github_login: true })
  vi.stubGlobal('navigator', { userAgent: 'Mozilla/5.0 (iPhone)', platform: 'iPhone' })
})

it('uses the invite as the primary mobile login and emits success', async () => {
  loginWithInvite.mockResolvedValue({
    user_id: 'user-1', email: 'owner@example.com', display_name: 'owner',
  })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/assistant', component: { template: '<p />' } }],
  })
  await router.push('/assistant')
  await router.isReady()
  const wrapper = mount(CloudAuthGate, {
    props: { checking: false },
    global: { plugins: [router] },
  })
  await vi.waitFor(() => expect(wrapper.text()).toContain('使用邀请码进入'))

  await wrapper.get('input').setValue('one-time-code')
  await wrapper.get('form').trigger('submit')

  await vi.waitFor(() => expect(loginWithInvite).toHaveBeenCalledWith(
    'one-time-code', 'iPhone Safari',
  ))
  expect(wrapper.emitted('authenticated')).toHaveLength(1)
  expect(wrapper.text()).toContain('改用 GitHub 登录')
})
