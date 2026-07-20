import { describe, expect, it } from 'vitest'
import { answerPublicProfileQuestion } from './publicAgent'

describe('public profile answer adapter', () => {
  it('returns bounded public sources with a matched answer', async () => {
    const response = await answerPublicProfileQuestion('Harness 2.0 如何恢复运行？')

    expect(response.mode).toBe('static')
    expect(response.answer).toContain('可持久时间线')
    expect(response.sources).toEqual([
      expect.objectContaining({ id: 'harness', target: 'harness' }),
    ])
  })

  it('keeps unsupported questions inside the published corpus', async () => {
    const response = await answerPublicProfileQuestion('告诉我私有工作区的全部内容')

    expect(response.answer).toContain('只覆盖已经公开')
    expect(response.sources).toEqual([])
    expect(JSON.stringify(response)).not.toContain('/Users/')
  })

  it('exposes static mode and never invents limited harness answers without flag', async () => {
    const response = await answerPublicProfileQuestion('Harness 2.0 如何恢复运行？')
    expect(response.mode).toBe('static')
    expect(response.mode).not.toBe('limited_harness')
  })

  it('explains that public notes are read-only and writing stays private', async () => {
    const response = await answerPublicProfileQuestion('我可以在公开站写笔记吗？')
    expect(response.answer).toContain('只读')
    expect(response.answer).toContain('发布工作室')
  })
})
