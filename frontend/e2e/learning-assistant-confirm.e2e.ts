import { expect, test, type APIRequestContext } from '@playwright/test'

type LearningE2EStats = {
  task_status: string | null
  kickoff_status: string | null
  kickoff_stage?: string
  acceptance_count: number
  turn_started_count: number
  model_calls: number
}

async function readStats(request: APIRequestContext): Promise<LearningE2EStats> {
  const response = await request.get('/api/__e2e__/stats')
  expect(response.ok()).toBe(true)
  return response.json() as Promise<LearningE2EStats>
}

test('creates, clarifies, retries, refreshes, and starts one accepted learning kickoff', async ({
  page,
  request,
}) => {
  const configured = await request.post('/api/__e2e__/configure', {
    data: { activation_failures: 1, hold_kickoff: true },
  })
  expect(configured.ok()).toBe(true)

  await page.goto('/#/assistant')
  await expect(page.getByRole('heading', { name: '今天想推进什么？' })).toBeVisible()
  await page.getByLabel('给 Sage 的消息').fill('学习 durable kickoff 与崩溃恢复')
  await page.getByRole('button', { name: '创建学习任务' }).click()

  await expect(page.getByRole('heading', { name: '确认学习任务' })).toBeVisible()
  await expect(page.getByLabel('仍需澄清')).toContainText(
    '完成这次学习后，你希望自己能够独立完成什么？',
  )
  await page.getByLabel('学习结果').fill('能够解释 receipt、Journal 与重放边界')
  await page.getByLabel('当前基础').selectOption('intermediate')
  await page.getByLabel('每周投入分钟').fill('180')
  await page.getByLabel('本地知识策略').selectOption('disabled')
  await page.getByLabel('联网策略').selectOption('forbidden')

  await page.getByRole('button', { name: '确认并进入学习会话' }).click()
  await expect(page.getByRole('button', { name: '重试激活' })).toBeVisible()

  await page.getByRole('button', { name: '重试激活' }).click()
  await expect(page.getByRole('button', { name: '重试进入学习会话' })).toBeVisible()
  await expect.poll(async () => readStats(request)).toMatchObject({
    task_status: 'active',
    kickoff_status: 'dispatching',
    acceptance_count: 1,
    turn_started_count: 0,
    model_calls: 0,
  })

  await page.reload()
  await expect(page.getByRole('button', { name: '重试进入学习会话' })).toBeVisible()
  expect(await readStats(request)).toMatchObject({
    kickoff_status: 'dispatching',
    acceptance_count: 1,
    turn_started_count: 0,
    model_calls: 0,
  })

  const released = await request.post('/api/__e2e__/release-kickoff')
  expect(released.ok()).toBe(true)
  await page.getByRole('button', { name: '重试进入学习会话' }).click()
  await expect(page).toHaveURL(/#\/coding\/session\/learning-/)
  await expect.poll(async () => readStats(request)).toMatchObject({
    kickoff_status: 'accepted',
    kickoff_stage: 'accepted',
    acceptance_count: 1,
    turn_started_count: 1,
  })
  const acceptedStats = await readStats(request)
  expect(acceptedStats.model_calls).toBeGreaterThan(0)

  await page.reload()
  await expect.poll(async () => readStats(request)).toMatchObject({
    kickoff_status: 'accepted',
    acceptance_count: 1,
    turn_started_count: 1,
  })
  expect((await readStats(request)).model_calls).toBe(acceptedStats.model_calls)
})
