import { describe, expect, it } from 'vitest'
import { projectCodingReviewBundle } from './codingReviewBundle'

describe('projectCodingReviewBundle', () => {
  it('projects evidence, practice and deposit facts for one selected run', () => {
    const bundle = projectCodingReviewBundle({
      runId: 'run-1',
      turn: {
        run_id: 'run-1',
        tools: [
          {
            id: 'tool-1',
            tool: 'knowledge_search',
            args: { query: 'Harness 恢复' },
            status: 'completed',
            result: '',
            is_error: false,
            retrieval: {
              status: 'evidence_found',
              query: 'Harness 恢复',
              usedTokens: 420,
              tokenBudget: 2_000,
              omittedCount: 0,
              citations: [{
                citationId: 'cite-1',
                rank: 1,
                pageRevision: 'page-rev-1',
                sourceRevision: 'source-rev-1',
                sourceRelativePath: 'docs/harness.md',
                title: 'Harness 恢复机制',
                headingPath: ['恢复'],
                excerpt: '恢复必须从 durable timeline 重建。',
                truncated: false,
              }],
            },
          },
          {
            id: 'tool-2', tool: 'run_shell', args: {}, status: 'completed',
            result: 'passed', is_error: false,
          },
        ],
      },
      run: {
        run_id: 'run-1', status: 'completed', event_count: 8, tool_count: 2,
        error_count: 0, last_event_type: 'run_finished', started_at: '', updated_at: '',
        audit: {
          run_id: 'run-1', status: 'completed', headline: '验证完成', tool_count: 2,
          completed_tool_count: 2, failed_tool_count: 0, approval_count: 1,
          duration_ms: 2_500, changed_files: ['frontend/src/App.vue'], steps: [],
        },
      },
      diff: {
        run_id: 'run-1', changed_files: ['frontend/src/App.vue'], file_count: 1, truncated: false,
      },
      memoryProposals: [{
        proposal_id: 'proposal-1', workspace_id: 'workspace', session_id: 'session',
        run_id: 'run-1', reflection_id: 'reflection-1', status: 'pending',
        projection_status: 'pending', revision: 2, base_revision: 1, candidate_count: 1,
        candidates: [{
          content: '恢复必须由 timeline 真事实驱动。', topic: 'harness',
          source: 'reflection', source_ref: 'run-1', created_at: '',
        }],
        created_at: '2026-07-18T10:00:00Z', updated_at: '2026-07-18T10:00:00Z',
      }],
    })

    expect(bundle.runId).toBe('run-1')
    expect(bundle.evidence.status).toBe('ready')
    expect(bundle.evidence.items).toEqual([expect.objectContaining({
      id: 'cite-1', title: 'Harness 恢复机制', source: 'docs/harness.md',
    })])
    expect(bundle.practice).toMatchObject({
      status: 'complete', headline: '验证完成', toolCount: 2,
      completedToolCount: 2, changedFiles: ['frontend/src/App.vue'],
    })
    expect(bundle.deposit).toMatchObject({
      status: 'review', proposalId: 'proposal-1', revision: 2,
      items: ['恢复必须由 timeline 真事实驱动。'],
    })
  })

  it('keeps absent facts explicit instead of inventing a learning result', () => {
    const bundle = projectCodingReviewBundle({
      runId: 'run-empty',
      turn: null,
      run: null,
      diff: null,
      memoryProposals: [],
    })

    expect(bundle.evidence.status).toBe('empty')
    expect(bundle.practice.status).toBe('empty')
    expect(bundle.deposit.status).toBe('empty')
    expect(bundle.deposit.items).toEqual([])
  })
})
