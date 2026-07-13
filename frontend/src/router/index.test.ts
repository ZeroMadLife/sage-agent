import { beforeEach, describe, expect, it } from 'vitest'
import router from './index'

describe('settings router', () => {
  beforeEach(async () => {
    await router.replace('/coding')
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
})
