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
      page_number: 2,
      block_kind: 'table',
      bbox: [0.1, 0.2, 0.9, 0.8],
      bbox_coordinate_space: 'normalized',
      media_ref: 'charts/retrieval.png',
      confidence: 0.93,
      parser_id: 'qwen3-vl',
      parser_version: '2.0.0',
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
      pageNumber: 2,
      blockKind: 'table',
      bbox: [0.1, 0.2, 0.9, 0.8],
      bboxCoordinateSpace: 'normalized',
      mediaRef: 'charts/retrieval.png',
      confidence: 0.93,
      parserId: 'qwen3-vl',
      parserVersion: '2.0.0',
    })
    expect(result?.citations[0].excerpt.length).toBeLessThanOrEqual(1200)
  })

  it('drops invalid visual coordinates instead of projecting them', () => {
    const result = parseKnowledgeRetrieval(JSON.stringify({
      status: 'evidence_found',
      citations: [{
        citation_id: 'kcite_bad_bbox',
        page_revision: 'krev_bad_bbox',
        title: 'Bad region',
        excerpt: 'bounded evidence',
        bbox: [-1, 0, 2, 1],
        bbox_coordinate_space: 'pixels',
        confidence: 9,
      }],
    }))

    expect(result?.citations[0].bbox).toBeUndefined()
    expect(result?.citations[0].bboxCoordinateSpace).toBeUndefined()
    expect(result?.citations[0].confidence).toBeUndefined()
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
