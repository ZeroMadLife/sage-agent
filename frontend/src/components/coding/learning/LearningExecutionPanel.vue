<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { AlertTriangle, BookOpen, Check, FlaskConical, Play, RefreshCw, Sparkles } from 'lucide-vue-next'
import {
  advanceLearningTask,
  fetchLearningArtifact,
  fetchLearningResume,
  isLearningRequestStatus,
} from '../../../api/assistant'
import type { LearningArtifactResponse, LearningResumeResponse } from '../../../types/api'

const props = defineProps<{ taskId: string }>()
const resume = ref<LearningResumeResponse | null>(null)
const artifact = ref<LearningArtifactResponse | null>(null)
const loading = ref(false)
const advancing = ref(false)
const error = ref('')
let generation = 0

const nodes = computed(() => {
  const stage = resume.value?.stage || ''
  const knowledgeDone = !['', 'knowledge_pending'].includes(stage)
  const researchActive = stage === 'research_pending'
  const researchDone = ['research_ready', 'synthesize_pending', 'artifact_ready'].includes(stage)
  const synthesizeActive = stage === 'synthesize_pending'
  const synthesizeDone = stage === 'artifact_ready'
  return [
    { id: 'knowledge', label: 'Knowledge', icon: BookOpen, state: knowledgeDone ? 'done' : stage === 'knowledge_pending' ? 'active' : 'pending' },
    { id: 'research', label: 'Research', icon: FlaskConical, state: researchDone ? 'done' : researchActive ? 'active' : 'pending' },
    { id: 'synthesize', label: 'Synthesize', icon: Sparkles, state: synthesizeDone ? 'done' : synthesizeActive ? 'active' : 'pending' },
  ]
})

const artifactId = computed(() => resume.value?.artifact?.artifact_id || '')
const canAdvance = computed(() => !resume.value || !['artifact_ready', 'blocked'].includes(resume.value.stage))

async function load() {
  const taskId = props.taskId
  const current = ++generation
  loading.value = true
  error.value = ''
  try {
    const next = await fetchLearningResume(taskId)
    if (!ownsRequest(taskId, current)) return
    resume.value = next
    await loadArtifact(next, taskId, current)
  } catch (cause) {
    if (!ownsRequest(taskId, current)) return
    if (isLearningRequestStatus(cause, 404)) {
      resume.value = null
      artifact.value = null
    } else {
      error.value = cause instanceof Error ? cause.message : '学习进度加载失败'
    }
  } finally {
    if (ownsRequest(taskId, current)) loading.value = false
  }
}

function ownsRequest(taskId: string, current: number) {
  return taskId === props.taskId && current === generation
}

async function loadArtifact(summary: LearningResumeResponse, taskId: string, current: number) {
  if (!summary.artifact?.artifact_id) {
    if (ownsRequest(taskId, current)) artifact.value = null
    return
  }
  const next = await fetchLearningArtifact(taskId, summary.artifact.artifact_id)
  if (ownsRequest(taskId, current)) artifact.value = next
}

async function advance() {
  if (advancing.value) return
  const taskId = props.taskId
  const current = generation
  advancing.value = true
  error.value = ''
  const revision = resume.value?.checkpoint_revision ?? 0
  try {
    const next = await advanceLearningTask(
      taskId,
      revision,
      `learning-ui-${taskId}-checkpoint-${revision}`,
    )
    if (!ownsRequest(taskId, current)) return
    resume.value = next
    await loadArtifact(next, taskId, current)
  } catch (cause) {
    if (!ownsRequest(taskId, current)) return
    error.value = cause instanceof Error ? cause.message : '学习进度推进失败'
  } finally {
    if (ownsRequest(taskId, current)) advancing.value = false
  }
}

watch(() => props.taskId, () => {
  advancing.value = false
  void load()
}, { immediate: true })
</script>

<template>
  <section class="learning-execution" aria-label="学习任务执行状态">
    <header>
      <div><strong>{{ resume?.goal_summary || '学习任务' }}</strong><span v-if="resume">Checkpoint {{ resume.checkpoint_revision }} · {{ resume.stage }}</span></div>
      <div class="learning-actions">
        <button type="button" aria-label="刷新学习进度" title="刷新学习进度" :disabled="loading" @click="load"><RefreshCw :size="15" :class="{ spin: loading }" /></button>
        <button v-if="canAdvance" class="advance-button" type="button" :disabled="advancing || loading" @click="advance"><Play :size="14" />{{ resume ? '继续生成' : '开始生成' }}</button>
      </div>
    </header>

    <p v-if="error" class="learning-error" role="alert"><AlertTriangle :size="15" />{{ error }}</p>
    <ol v-if="resume" class="learning-dag" :data-dag-hash="resume.dag_hash">
      <li v-for="node in nodes" :key="node.id" :class="node.state">
        <span><Check v-if="node.state === 'done'" :size="14" /><component :is="node.icon" v-else :size="15" /></span>
        <strong>{{ node.label }}</strong>
      </li>
    </ol>

    <div v-if="resume" class="learning-status">
      <span>证据 {{ resume.evidence_count }}</span><span>引用 {{ resume.citation_count }}</span><span>下一步 {{ resume.next_action }}</span>
      <span v-if="artifactId">Artifact {{ artifactId }}</span>
    </div>
    <p v-if="resume?.blocking_reason" class="learning-blocker">{{ resume.blocking_reason }}</p>

    <div v-if="artifact" class="learning-artifact">
      <pre>{{ artifact.content }}</pre>
      <ul v-if="artifact.citations.length" aria-label="学习材料来源">
        <li v-for="citation in artifact.citations" :key="citation.evidence_ref">
          <a v-if="citation.url" :href="citation.url" target="_blank" rel="noopener noreferrer">{{ citation.title }}</a>
          <span v-else>{{ citation.title }} · {{ citation.evidence_ref }}</span>
        </li>
      </ul>
    </div>
  </section>
</template>

<style scoped>
.learning-execution{box-sizing:border-box;flex:none;width:100%;max-width:100%;min-width:0;overflow:hidden;border-bottom:1px solid var(--sage-border);background:var(--sage-surface-muted)}
.learning-execution>header{display:flex;align-items:center;justify-content:space-between;gap:16px;min-height:52px;padding:8px 18px}
.learning-execution>header>div:first-child{display:grid;gap:2px;min-width:0}.learning-execution header strong{overflow:hidden;font-size:13px;text-overflow:ellipsis;white-space:nowrap}.learning-execution header span{color:var(--sage-text-muted);font-size:10px}
.learning-actions{display:flex;align-items:center;gap:6px}.learning-actions button{display:inline-flex;align-items:center;justify-content:center;min-width:30px;height:30px;border:1px solid var(--sage-border);border-radius:6px;color:var(--sage-text-muted);background:var(--sage-surface)}.learning-actions .advance-button{gap:5px;padding:0 10px;color:var(--sage-brand-strong);font-size:11px;font-weight:650}
.learning-dag{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));margin:0;padding:0 18px 10px;list-style:none}.learning-dag li{display:flex;align-items:center;gap:7px;min-width:0;color:var(--sage-text-muted);font-size:11px}.learning-dag li:not(:last-child)::after{flex:1;height:1px;margin:0 9px;background:var(--sage-border);content:''}.learning-dag li>span{display:grid;place-items:center;width:24px;height:24px;border:1px solid var(--sage-border-strong);border-radius:50%}.learning-dag li.active{color:var(--sage-source)}.learning-dag li.done{color:var(--sage-success)}
.learning-status{display:flex;gap:12px;overflow:auto;padding:0 18px 9px;color:var(--sage-text-muted);font-size:10px;white-space:nowrap}.learning-error,.learning-blocker{display:flex;align-items:center;gap:6px;margin:0;padding:0 18px 9px;color:var(--sage-danger);font-size:11px}.learning-artifact{max-height:210px;min-width:0;overflow:auto;border-top:1px solid var(--sage-border);padding:10px 18px;background:var(--sage-surface)}.learning-artifact pre{max-width:100%;margin:0;overflow-wrap:anywhere;color:var(--sage-text);font:11px/1.55 var(--sage-font-mono);white-space:pre-wrap}.learning-artifact ul{display:flex;flex-wrap:wrap;gap:6px 12px;min-width:0;margin:8px 0 0;padding:0;list-style:none;font-size:10px}.learning-artifact li{min-width:0;overflow-wrap:anywhere}.learning-artifact a{color:var(--sage-source)}
.spin{animation:learning-spin 1s linear infinite}@keyframes learning-spin{to{transform:rotate(360deg)}}
@media(max-width:640px){.learning-execution>header{padding-right:12px;padding-left:12px}.learning-dag{padding-right:12px;padding-left:12px}.learning-dag li:not(:last-child)::after{margin:0 4px}.learning-status{padding-right:12px;padding-left:12px}.learning-artifact{padding-right:12px;padding-left:12px}}
@media(prefers-reduced-motion:reduce){.spin{animation:none}}
</style>
