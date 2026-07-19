import type { CodingConnectionState } from '../stores/codingStream'
import type { HarnessProjection } from './types'

export type HarnessRunVisualState =
  | 'idle'
  | 'running'
  | 'tool'
  | 'approval'
  | 'failed'
  | 'completed'
  | 'recovering'

export function harnessRunVisualState(
  projection: HarnessProjection,
  connectionState: CodingConnectionState,
): HarnessRunVisualState {
  if (connectionState === 'recovering') return 'recovering'
  if (projection.status === 'failed' || projection.status === 'cancelled') return 'failed'
  if (projection.status === 'blocked') return 'approval'
  if (projection.status === 'completed') return 'completed'
  if (projection.status === 'running') {
    const active = projection.stages.find((stage) => stage.id === projection.activeStageId)
    if (active?.id === 'act' || active?.operationRef) return 'tool'
    return 'running'
  }
  return 'idle'
}
