import { describe, expect, it } from 'vitest'
import { getSiteMeta, listNotes, parseNoteMarkdown } from './content'

describe('public content', () => {
  it('reads site meta repository url', () => {
    expect(getSiteMeta().githubRepoUrl).toContain('ZeroMadLife/sage-agent')
  })

  it('parses note frontmatter without executing html', () => {
    const note = parseNoteMarkdown(`---
title: 测试笔记
date: 2026-07-20
summary: 摘要
tags: [harness]
---

## 标题

正文 <script>alert(1)</script>
`, 'test-note')
    expect(note.slug).toBe('test-note')
    expect(note.title).toBe('测试笔记')
    expect(note.tags).toEqual(['harness'])
    expect(note.body).toContain('正文')
    expect(note.body).toContain('<script>alert(1)</script>')
  })

  it('lists engineering notes from static content', () => {
    const notes = listNotes()
    expect(notes.length).toBeGreaterThanOrEqual(3)
    expect(notes.some((note) => note.slug === 'why-durable-timeline')).toBe(true)
    expect(notes.some((note) => note.slug === 'public-site-is-read-only')).toBe(true)
  })
})
