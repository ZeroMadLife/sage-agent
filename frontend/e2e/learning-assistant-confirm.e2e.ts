import { expect, test, type APIRequestContext } from '@playwright/test'

type LearningE2EStats = {
  task_status: string | null
  session_id: string | null
  kickoff_status: string | null
  kickoff_stage?: string
  acceptance_count: number
  turn_started_count: number
  model_calls: number
}

type L3Summary = {
  task_id: string
  task_revision: number
  goal_summary: string
  plan_id: string
  plan_hash: string
  dag_hash: string
  stage: string
  evidence_count: number
  citation_count: number
  gap_codes: string[]
  blocking_reason: string
  next_action: string
  artifact_ref: string
  artifact: null | {
    artifact_id: string
    kind: string
    content_hash: string
    media_type: string
    status: string
    citation_count: number
    source_revisions: string[]
    retention: string
  }
  checkpoint_revision: number
  fencing_token: number
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

test('renders source gap, conditional Research, deduplicated Artifact, citation, failure and refresh', async ({
  page,
  request,
}, testInfo) => {
  const stats = await readStats(request)
  expect(stats.session_id).toBeTruthy()
  const taskId = 'ltask-e2e-l3'
  let current: L3Summary | null = null
  const replays = new Map<string, L3Summary>()

  const summary = (
    stage: string,
    checkpointRevision: number,
    options: { grounded?: boolean; blocked?: boolean } = {},
  ): L3Summary => {
    const grounded = options.grounded === true
    const artifactId = grounded ? 'lart-e2e-final' : 'lart-e2e-gap'
    return {
      task_id: taskId,
      task_revision: 1,
      goal_summary: '学习 durable checkpoint',
      plan_id: 'lplan-e2e',
      plan_hash: 'sha256:plan-e2e',
      dag_hash: 'sha256:dag-e2e',
      stage,
      evidence_count: grounded ? 1 : 0,
      citation_count: grounded ? 1 : 0,
      gap_codes: options.blocked ? ['learning_research_timeout'] : grounded ? [] : ['knowledge_no_evidence'],
      blocking_reason: options.blocked ? 'learning_research_timeout' : '',
      next_action: options.blocked ? 'resolve_blocker' : stage === 'artifact_ready' ? 'review_artifact' : stage.includes('research') ? 'research' : 'synthesize',
      artifact_ref: `sage://learning/artifacts/${artifactId}`,
      artifact: {
        artifact_id: artifactId,
        kind: 'learning_map',
        content_hash: grounded ? 'sha256:final' : 'sha256:gap',
        media_type: 'text/markdown',
        status: grounded ? 'ready' : 'source_gap',
        citation_count: grounded ? 1 : 0,
        source_revisions: grounded ? ['sha256:web-r1'] : [],
        retention: 'task',
      },
      checkpoint_revision: checkpointRevision,
      fencing_token: checkpointRevision,
    }
  }

  await page.route(`**/api/v1/learning/tasks/*/resume`, async (route) => {
    if (!current) {
      await route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: { code: 'learning_resume_not_found' } }) })
      return
    }
    await route.fulfill({ json: current })
  })
  await page.route(`**/api/v1/learning/tasks/*/advance`, async (route) => {
    const key = route.request().headers()['idempotency-key'] || ''
    const replay = replays.get(key)
    if (replay) {
      await route.fulfill({ json: replay })
      return
    }
    const revision = Number((route.request().postDataJSON() as { expected_checkpoint_revision: number }).expected_checkpoint_revision)
    current = revision === 0 ? summary('knowledge_pending', 1)
      : revision === 1 ? summary('source_gap', 2)
        : revision === 2 ? summary('research_pending', 3)
          : revision === 3 ? summary('research_ready', 4, { grounded: true })
            : revision === 4 ? summary('synthesize_pending', 5, { grounded: true })
              : summary('artifact_ready', 6, { grounded: true })
    replays.set(key, current)
    await route.fulfill({ json: current })
  })
  await page.route(`**/api/v1/learning/tasks/*/artifacts/*`, async (route) => {
    const final = route.request().url().includes('lart-e2e-final')
    await route.fulfill({ json: {
      artifact_id: final ? 'lart-e2e-final' : 'lart-e2e-gap',
      artifact_ref: `sage://learning/artifacts/${final ? 'lart-e2e-final' : 'lart-e2e-gap'}`,
      kind: 'learning_map', task_id: taskId, task_revision: 1, plan_id: 'lplan-e2e',
      content_hash: final ? 'sha256:final' : 'sha256:gap', media_type: 'text/markdown',
      status: final ? 'ready' : 'source_gap', evidence_refs: final ? ['wcite-e2e'] : [],
      source_revisions: final ? ['sha256:web-r1'] : [], retention: 'task',
      content: final ? '# 学习地图\n\n来源状态：已支持。\n' : '# 学习地图\n\n来源状态：source_gap。\n',
      citations: final ? [{ evidence_ref: 'wcite-e2e', title: 'Checkpoint docs', url: 'https://docs.example.com/checkpoint', content_hash: 'sha256:web-r1', fetched_at: '2026-08-25T01:00:00Z', page_revision: 'sha256:web-r1', source_revision: 'sha256:web-r1' }] : [],
      created_at: '2026-08-25T00:00:00Z', updated_at: '2026-08-25T00:00:00Z',
    } })
  })

  await page.goto(`/#/coding/session/${stats.session_id}`)
  const panel = page.getByLabel('学习任务执行状态')
  await expect(panel).toBeVisible()
  await panel.getByRole('button', { name: '开始生成' }).click()
  await expect(panel).toContainText('knowledge_pending')
  await panel.getByRole('button', { name: '继续生成' }).click()
  await expect(panel).toContainText('source_gap')
  await panel.getByRole('button', { name: '继续生成' }).click()
  await expect(panel).toContainText('research_pending')
  await panel.getByRole('button', { name: '继续生成' }).click()
  await expect(panel.getByRole('link', { name: 'Checkpoint docs' })).toBeVisible()
  await panel.getByRole('button', { name: '继续生成' }).click()
  await expect(panel).toContainText('synthesize_pending')
  await panel.getByRole('button', { name: '继续生成' }).click()
  await expect(panel).toContainText('artifact_ready')
  await expect(panel).toContainText('Artifact lart-e2e-final')

  const duplicate = await page.evaluate(async ({ id, revision }) => {
    const response = await fetch(`/api/v1/learning/tasks/${id}/advance`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': `learning-ui-${id}-checkpoint-${revision}` },
      body: JSON.stringify({ expected_checkpoint_revision: revision }),
    })
    return response.json()
  }, { id: taskId, revision: 5 })
  expect(duplicate.artifact.artifact_id).toBe('lart-e2e-final')

  await page.reload()
  await expect(panel).toContainText('artifact_ready')
  await expect(panel.getByRole('link', { name: 'Checkpoint docs' })).toBeVisible()
  await page.screenshot({
    path: testInfo.outputPath('learning-artifact-ready.png'),
    fullPage: true,
  })
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(panel).toBeVisible()
  const panelBox = await panel.boundingBox()
  expect(panelBox?.width).toBeLessThanOrEqual(390)
  await page.screenshot({
    path: testInfo.outputPath('learning-artifact-ready-mobile.png'),
    fullPage: true,
  })

  current = summary('blocked', 7, { blocked: true })
  await page.reload()
  await expect(panel).toContainText('learning_research_timeout')
  await expect(panel.getByRole('button', { name: '继续生成' })).toHaveCount(0)
})
