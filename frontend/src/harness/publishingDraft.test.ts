import { describe, expect, it } from 'vitest'
import {
  defaultPublishingDraft,
  draftWordCount,
  loadPublishingDraft,
  savePublishingDraft,
} from './publishingDraft'

describe('publishingDraft', () => {
  it('persists an editable local draft without claiming it is published', () => {
    const storage = window.localStorage
    storage.clear()
    expect(loadPublishingDraft(storage).title).toBe(defaultPublishingDraft.title)
    savePublishingDraft({ ...defaultPublishingDraft, title: '新的标题' }, storage)
    expect(loadPublishingDraft(storage).title).toBe('新的标题')
  })

  it('counts Chinese and latin writing for the editor status', () => {
    expect(draftWordCount('可靠 Agent recovery loop')).toBe(5)
  })
})
