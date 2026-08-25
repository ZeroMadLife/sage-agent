import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

type LearningE2EStats = {
  task_count: number
  task_status: string | null
  session_id: string | null
  kickoff_status: string | null
  kickoff_stage?: string
  acceptance_count: number
  turn_started_count: number
  model_calls: number
  research_model_calls: number
  knowledge_hold_started: boolean
  pid: number
}

type L3Summary = {
  task_id: string
  stage: string
  evidence_count: number
  citation_count: number
  blocking_reason: string
  checkpoint_revision: number
  artifact_ref: string
  artifact: null | {
    artifact_id: string
    status: string
    citation_count: number
  }
}

type LearningArtifact = {
  artifact_id: string
  status: string
  content_hash: string
  citations: Array<{ evidence_ref: string; title: string; url: string }>
}

type LearningTaskHandle = {
  taskId: string
  sessionId: string
}

type SourcePolicy = {
  knowledge: 'preferred' | 'required' | 'disabled'
  web: 'allowed_when_insufficient' | 'forbidden'
  domains: string[]
  freshness: 'all' | 'current'
}

async function readStats(request: APIRequestContext): Promise<LearningE2EStats> {
  const response = await request.get('/api/__e2e__/stats')
  expect(response.ok()).toBe(true)
  return response.json() as Promise<LearningE2EStats>
}

async function createLearningTask(
  request: APIRequestContext,
  topic: string,
  sourcePolicy: SourcePolicy,
): Promise<LearningTaskHandle> {
  const draftResponse = await request.post('/api/v1/learning/tasks/draft', {
    data: {
      topic,
      desired_outcome: `能够基于证据解释 ${topic}`,
      starting_level: 'beginner',
      time_budget_minutes_per_week: 120,
      source_policy: sourcePolicy,
    },
  })
  expect(draftResponse.status(), await draftResponse.text()).toBe(201)
  const draft = await draftResponse.json() as { task_id: string; task_revision: number }
  const activationResponse = await request.post(
    `/api/v1/learning/tasks/${draft.task_id}/activate`,
    {
      headers: { 'Idempotency-Key': `activate-${draft.task_id}` },
      data: { expected_revision: draft.task_revision },
    },
  )
  expect(activationResponse.ok(), await activationResponse.text()).toBe(true)
  const activation = await activationResponse.json() as { session_id: string; task_revision: number }
  const kickoffResponse = await request.post(
    `/api/v1/learning/tasks/${draft.task_id}/kickoff`,
    {
      headers: { 'Idempotency-Key': `kickoff-${draft.task_id}` },
      data: { expected_revision: activation.task_revision },
    },
  )
  expect(kickoffResponse.ok(), await kickoffResponse.text()).toBe(true)
  const kickoff = await kickoffResponse.json() as { session_id: string }
  return { taskId: draft.task_id, sessionId: kickoff.session_id }
}

async function advance(
  request: APIRequestContext,
  taskId: string,
  revision: number,
  key: string,
): Promise<L3Summary> {
  const response = await request.post(`/api/v1/learning/tasks/${taskId}/advance`, {
    headers: { 'Idempotency-Key': key },
    data: { expected_checkpoint_revision: revision },
  })
  expect(response.ok(), await response.text()).toBe(true)
  return response.json() as Promise<L3Summary>
}

async function advanceUntilTerminal(
  request: APIRequestContext,
  taskId: string,
): Promise<{ summaries: L3Summary[]; keys: string[] }> {
  const summaries: L3Summary[] = []
  const keys: string[] = []
  let revision = 0
  for (let index = 0; index < 8; index += 1) {
    const key = `e2e-${taskId}-checkpoint-${revision}`
    const summary = await advance(request, taskId, revision, key)
    summaries.push(summary)
    keys.push(key)
    revision = summary.checkpoint_revision
    if (['artifact_ready', 'blocked'].includes(summary.stage)) return { summaries, keys }
  }
  throw new Error(`Learning task ${taskId} did not reach a terminal stage`)
}

async function readArtifact(
  request: APIRequestContext,
  taskId: string,
  artifactId: string,
): Promise<LearningArtifact> {
  const response = await request.get(
    `/api/v1/learning/tasks/${taskId}/artifacts/${artifactId}`,
  )
  expect(response.ok(), await response.text()).toBe(true)
  return response.json() as Promise<LearningArtifact>
}

async function openLearningPanel(page: Page, handle: LearningTaskHandle) {
  await page.goto(`/#/coding/session/${handle.sessionId}`)
  const panel = page.getByLabel('学习任务执行状态')
  await expect(panel).toBeVisible()
  return panel
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

test('uses real API and SQLite for Knowledge, Research, conflict, replay and restart', async ({
  page,
  request,
}, testInfo) => {
  const forbidden: SourcePolicy = {
    knowledge: 'preferred', web: 'forbidden', domains: [], freshness: 'all',
  }
  const webAllowed: SourcePolicy = {
    knowledge: 'disabled', web: 'allowed_when_insufficient', domains: ['example.com'], freshness: 'current',
  }
  const knowledge = await createLearningTask(request, 'Knowledge 已支持 checkpoint', forbidden)
  const sourceGap = await createLearningTask(request, '无授权来源的 source gap', {
    ...forbidden, knowledge: 'disabled',
  })
  const research = await createLearningTask(request, '条件 Research 成功', webAllowed)
  const researchFailure = await createLearningTask(request, 'Research 失败', webAllowed)
  const conflict = await createLearningTask(request, 'Knowledge 冲突 checkpoint', forbidden)
  const sameUrlConflict = await createLearningTask(request, '同 URL 冲突 Research', webAllowed)
  const orphan = await createLearningTask(request, '崩溃 takeover 恢复', {
    ...forbidden, knowledge: 'disabled',
  })

  const knowledgeRun = await advanceUntilTerminal(request, knowledge.taskId)
  expect(knowledgeRun.summaries.map(item => item.stage)).toEqual([
    'knowledge_pending', 'knowledge_ready', 'synthesize_pending', 'artifact_ready',
  ])
  const knowledgeFinal = knowledgeRun.summaries.at(-1)!
  expect(knowledgeFinal.artifact).toMatchObject({ status: 'ready', citation_count: 1 })

  const gapRun = await advanceUntilTerminal(request, sourceGap.taskId)
  expect(gapRun.summaries.map(item => item.stage)).toEqual([
    'knowledge_pending', 'source_gap', 'synthesize_pending', 'artifact_ready',
  ])
  expect(gapRun.summaries.at(-1)!.artifact).toMatchObject({
    status: 'source_gap', citation_count: 0,
  })

  const researchRun = await advanceUntilTerminal(request, research.taskId)
  expect(researchRun.summaries.map(item => item.stage)).toEqual([
    'knowledge_pending', 'source_gap', 'research_pending', 'research_ready',
    'synthesize_pending', 'artifact_ready',
  ])
  const researchReady = researchRun.summaries.find(item => item.stage === 'research_ready')!
  const researchFinal = researchRun.summaries.at(-1)!
  expect(researchFinal.artifact).toMatchObject({ status: 'ready', citation_count: 1 })
  expect(researchFinal.artifact!.artifact_id).toBe(researchReady.artifact!.artifact_id)
  const researchArtifact = await readArtifact(
    request, research.taskId, researchFinal.artifact!.artifact_id,
  )
  expect(researchArtifact.citations).toEqual([
    expect.objectContaining({
      evidence_ref: 'wcite_e2e_research',
      title: 'Checkpoint public docs',
      url: 'https://docs.example.com/checkpoint',
    }),
  ])

  const failureRun = await advanceUntilTerminal(request, researchFailure.taskId)
  expect(failureRun.summaries.at(-1)).toMatchObject({
    stage: 'blocked', blocking_reason: 'learning_research_provider_unavailable',
  })

  const conflictRun = await advanceUntilTerminal(request, conflict.taskId)
  const conflictFinal = conflictRun.summaries.at(-1)!
  expect(conflictFinal.artifact).toMatchObject({ status: 'unverified', citation_count: 2 })
  const conflictArtifact = await readArtifact(
    request, conflict.taskId, conflictFinal.artifact!.artifact_id,
  )
  expect(conflictArtifact.citations.map(item => item.title)).toEqual([
    'Knowledge Source A', 'Knowledge Source B',
  ])

  const sameUrlRun = await advanceUntilTerminal(request, sameUrlConflict.taskId)
  const sameUrlFinal = sameUrlRun.summaries.at(-1)!
  expect(sameUrlFinal.artifact).toMatchObject({ status: 'unverified', citation_count: 2 })
  const sameUrlArtifact = await readArtifact(
    request, sameUrlConflict.taskId, sameUrlFinal.artifact!.artifact_id,
  )
  expect(sameUrlArtifact.citations.map(item => item.url)).toEqual([
    'https://docs.example.com/checkpoint-conflict',
    'https://docs.example.com/checkpoint-conflict',
  ])
  expect(sameUrlArtifact.content).toContain('证据冲突未解决')

  const crossTaskArtifact = await request.get(
    `/api/v1/learning/tasks/${conflict.taskId}/artifacts/${researchArtifact.artifact_id}`,
  )
  expect(crossTaskArtifact.status()).toBe(404)

  const historicalReplay = await advance(
    request,
    research.taskId,
    0,
    researchRun.keys[0],
  )
  expect(historicalReplay).toEqual(researchRun.summaries[0])
  const currentResume = await request.get(`/api/v1/learning/tasks/${research.taskId}/resume`)
  expect(currentResume.ok()).toBe(true)
  expect((await currentResume.json() as L3Summary).stage).toBe('artifact_ready')

  const researchPanel = await openLearningPanel(page, research)
  await expect(researchPanel).toContainText('artifact_ready')
  await expect(researchPanel.getByRole('link', { name: 'Checkpoint public docs' })).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('learning-artifact-ready.png'), fullPage: true })

  const oldPid = (await readStats(request)).pid
  const orphanKey = `e2e-orphan-${orphan.taskId}`
  const orphaned = await request.post('/api/__e2e__/orphan-advance', {
    data: {
      task_id: orphan.taskId,
      idempotency_key: orphanKey,
      expected_checkpoint_revision: 0,
    },
  })
  expect(orphaned.ok(), await orphaned.text()).toBe(true)
  const restart = await request.post('/api/__e2e__/restart-process')
  expect(restart.status()).toBe(202)
  await expect.poll(async () => {
    try {
      const stats = await readStats(request)
      return stats.pid !== oldPid
    } catch {
      return false
    }
  }, { timeout: 45_000 }).toBe(true)

  const resumedAfterRestart = await request.get(`/api/v1/learning/tasks/${research.taskId}/resume`)
  expect(resumedAfterRestart.ok()).toBe(true)
  expect(await resumedAfterRestart.json()).toEqual(await currentResume.json())
  const artifactAfterRestart = await readArtifact(
    request, research.taskId, researchArtifact.artifact_id,
  )
  expect(artifactAfterRestart.content_hash).toBe(researchArtifact.content_hash)

  const takeover = await advance(request, orphan.taskId, 0, orphanKey)
  expect(takeover).toMatchObject({ stage: 'knowledge_pending', checkpoint_revision: 1 })
  const takeoverReplay = await advance(request, orphan.taskId, 0, orphanKey)
  expect(takeoverReplay).toEqual(takeover)

  await page.reload()
  await expect(researchPanel).toContainText('artifact_ready')
  await expect(researchPanel.getByRole('link', { name: 'Checkpoint public docs' })).toBeVisible()

  const conflictPanel = await openLearningPanel(page, conflict)
  await expect(conflictPanel).toContainText('artifact_ready')
  await expect(conflictPanel).toContainText('Knowledge Source A')
  await expect(conflictPanel).toContainText('Knowledge Source B')
  await page.setViewportSize({ width: 390, height: 844 })
  const panelBox = await conflictPanel.boundingBox()
  expect(panelBox?.width).toBeLessThanOrEqual(390)
  await page.screenshot({
    path: testInfo.outputPath('learning-conflict-unverified-mobile.png'),
    fullPage: true,
  })

  const failurePanel = await openLearningPanel(page, researchFailure)
  await expect(failurePanel).toContainText('learning_research_provider_unavailable')
  await expect(failurePanel.getByRole('button', { name: '继续生成' })).toHaveCount(0)
})

test('refresh takes over from a pending real advance without staying busy', async ({
  page,
  request,
}) => {
  const configured = await request.post('/api/__e2e__/configure', {
    data: { hold_knowledge: true },
  })
  expect(configured.ok()).toBe(true)
  const handle = await createLearningTask(request, '刷新 pending Knowledge', {
    knowledge: 'preferred', web: 'forbidden', domains: [], freshness: 'all',
  })
  const panel = await openLearningPanel(page, handle)
  const advanceButton = panel.getByRole('button', { name: '开始生成' })
  await expect(advanceButton).toBeEnabled()

  await advanceButton.click()
  await expect.poll(async () => (await readStats(request)).knowledge_hold_started).toBe(true)
  await panel.getByRole('button', { name: '刷新学习进度' }).click()
  await expect(panel.getByRole('button', { name: '开始生成' })).toBeEnabled()

  const released = await request.post('/api/__e2e__/release-knowledge')
  expect(released.ok()).toBe(true)
  await expect.poll(async () => {
    const response = await request.get(`/api/v1/learning/tasks/${handle.taskId}/resume`)
    return response.status()
  }).toBe(200)
  await expect(panel.getByRole('button', { name: '开始生成' })).toBeEnabled()

  await panel.getByRole('button', { name: '刷新学习进度' }).click()
  await expect(panel).toContainText('knowledge_pending')
})
