import { describe, expect, it } from 'vitest'
import { parseKnowledgeRetrieval } from './knowledgeRetrieval'

describe('knowledge retrieval projection', () => {
  it('parses revision-bound citations and clips untrusted result payloads', () => {
    const citations = Array.from({ length: 14 }, (_, index) => ({
      citation_id: `kcite_${index}`,
      rank: index + 1,
      page_revision: `krev_page_${index}`,
      source_revision: `krev_source_${index}`,
      source_kind: 'obsidian',
      source_relative_path: `notes/${index}.md`,
      title: `Note ${index}`,
      heading_path: ['Harness', 'Retrieval'],
      block_id: `block_${index}`,
      excerpt: '证据'.repeat(800),
      truncated: false,
    }))

    const result = parseKnowledgeRetrieval(JSON.stringify({
      status: 'evidence_found',
      query: 'Chat Harness',
      used_tokens: 640,
      token_budget: 1200,
      omitted_count: 2,
      citations,
    }))

    expect(result).not.toBeNull()
    expect(result?.citations).toHaveLength(12)
    expect(result?.citations[0]).toMatchObject({
      citationId: 'kcite_0',
      pageRevision: 'krev_page_0',
      sourceRevision: 'krev_source_0',
      sourceRelativePath: 'notes/0.md',
      headingPath: ['Harness', 'Retrieval'],
    })
    expect(result?.citations[0].excerpt.length).toBeLessThanOrEqual(1200)
  })

  it('fails closed for malformed or unrelated tool output', () => {
    expect(parseKnowledgeRetrieval('{bad json')).toBeNull()
    expect(parseKnowledgeRetrieval(JSON.stringify({ status: 'ok', citations: [] }))).toBeNull()
    expect(parseKnowledgeRetrieval(JSON.stringify({ status: 'evidence_found' }))).toBeNull()
    expect(parseKnowledgeRetrieval(JSON.stringify({
      status: 'evidence_found', citations: [{ citation_id: 'kcite_missing_revision' }],
    }))).toBeNull()
  })
})
