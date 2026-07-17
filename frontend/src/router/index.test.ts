import { beforeEach, describe, expect, it } from 'vitest'
import router from './index'

describe('settings router', () => {
  beforeEach(async () => {
    await router.replace('/assistant')
  })

  it('redirects empty and unknown settings sections to appearance', async () => {
    await router.push('/settings')
    expect(router.currentRoute.value.fullPath).toBe('/settings/appearance')

    await router.push('/settings/not-a-section')
    expect(router.currentRoute.value.fullPath).toBe('/settings/appearance')
  })

  it('keeps a known settings section', async () => {
    await router.push('/settings/memory')
    expect(router.currentRoute.value.fullPath).toBe('/settings/memory')
  })

  it('uses the personal assistant as the default and keeps coding deep links', async () => {
    await router.push('/')
    expect(router.currentRoute.value.fullPath).toBe('/assistant')

    await router.push('/coding/session/saved-session')
    expect(router.currentRoute.value.fullPath).toBe('/coding/session/saved-session')

    await router.push('/missing')
    expect(router.currentRoute.value.fullPath).toBe('/assistant')
  })

  it('keeps personal assistant routes inside the shared Sage shell', async () => {
    for (const path of ['/assistant', '/coding', '/coding/session/saved-session', '/knowledge', '/evolution', '/public']) {
      await router.push(path)
      expect(router.currentRoute.value.meta.assistantShell).toBe(true)
    }

    await router.push('/settings/appearance')
    expect(router.currentRoute.value.meta.assistantShell).not.toBe(true)
  })
})
