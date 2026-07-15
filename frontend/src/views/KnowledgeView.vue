<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { Check, FilePlus2, FolderUp, RotateCcw, RotateCw, Square, X } from 'lucide-vue-next'
import {
  buildKnowledgeJobStreamUrl,
  cancelKnowledgeJob,
  createKnowledgeJob,
  fetchKnowledgeJob,
  fetchKnowledgeJobs,
  fetchKnowledgePages,
  fetchKnowledgeProposals,
  fetchKnowledgeSummary,
  ingestKnowledgeSource,
  parseKnowledgeJobEvent,
  proposeKnowledgeRollback,
  transitionKnowledgeProposal,
  retryKnowledgeJobItem,
} from '../api/knowledge'
import { AssistantSectionView } from '../components/assistant'
import type { KnowledgeJob, KnowledgePage, KnowledgeProposal, KnowledgeWorkspaceSummary } from '../types/api'

const summary = ref<KnowledgeWorkspaceSummary | null>(null)
const proposals = ref<KnowledgeProposal[]>([])
const pages = ref<KnowledgePage[]>([])
const jobs = ref<KnowledgeJob[]>([])
const jobsAvailable = ref(true)
const loading = ref(true)
const error = ref('')
const relativePath = ref('')
const relativeDirectory = ref('.')
const selectedRoot = ref('')
const busy = ref<Record<string, boolean>>({})
let pollTimer: ReturnType<typeof setInterval> | null = null
const jobSockets = new Map<string, WebSocket>()

const activeJobs = computed(() => jobs.value.filter((job) => !isTerminal(job.status)))

const selectedSource = computed(() =>
  summary.value?.source_roots.find((item) => item.root_id === selectedRoot.value),
)

async function refresh() {
  loading.value = true
  error.value = ''
  try {
    const [nextSummary, nextProposals, nextPages] = await Promise.all([
      fetchKnowledgeSummary(),
      fetchKnowledgeProposals(),
      fetchKnowledgePages(),
    ])
    const nextJobs = await fetchKnowledgeJobs().catch(() => {
      jobsAvailable.value = false
      return []
    })
    summary.value = nextSummary
    proposals.value = nextProposals
    pages.value = nextPages
    jobs.value = nextJobs
    syncJobStreams()
    if (!selectedRoot.value) selectedRoot.value = nextSummary.source_roots[0]?.root_id || ''
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    loading.value = false
  }
}

async function createBatch() {
  if (!selectedRoot.value) return
  busy.value = { ...busy.value, batch: true }
  error.value = ''
  try {
    const job = await createKnowledgeJob(selectedRoot.value, relativeDirectory.value.trim() || '.')
    jobsAvailable.value = true
    jobs.value = [job, ...jobs.value]
    syncJobStreams()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    const next = { ...busy.value }
    delete next.batch
    busy.value = next
  }
}

async function refreshJobs() {
  if (!jobsAvailable.value) return
  try {
    jobs.value = await fetchKnowledgeJobs()
    syncJobStreams()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  }
}

async function cancelJob(job: KnowledgeJob) {
  const key = `cancel:${job.job_id}`
  busy.value = { ...busy.value, [key]: true }
  try {
    await cancelKnowledgeJob(job.job_id)
    await refreshJobs()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    const next = { ...busy.value }
    delete next[key]
    busy.value = next
  }
}

async function retryItem(job: KnowledgeJob, itemId: string) {
  const key = `retry:${itemId}`
  busy.value = { ...busy.value, [key]: true }
  try {
    await retryKnowledgeJobItem(job.job_id, itemId)
    await refreshJobs()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    const next = { ...busy.value }
    delete next[key]
    busy.value = next
  }
}

function syncJobStreams() {
  const active = new Set(activeJobs.value.map((job) => job.job_id))
  for (const [jobId, socket] of jobSockets) {
    if (!active.has(jobId)) {
      socket.close()
      jobSockets.delete(jobId)
    }
  }
  for (const job of activeJobs.value) {
    if (jobSockets.has(job.job_id)) continue
    const socket = new WebSocket(buildKnowledgeJobStreamUrl(job.job_id, job.latest_sequence))
    jobSockets.set(job.job_id, socket)
    socket.onmessage = async (message) => {
      try {
        const event = parseKnowledgeJobEvent(JSON.parse(String(message.data)), job.job_id)
        const current = jobs.value.find((item) => item.job_id === job.job_id)
        if (current && event.sequence <= current.latest_sequence) return
        const refreshed = await fetchKnowledgeJob(job.job_id, false)
        jobs.value = jobs.value.map((item) => item.job_id === job.job_id ? refreshed : item)
        if (isTerminal(refreshed.status)) {
          socket.close()
          await refreshJobs()
        }
      } catch (reason) {
        error.value = reason instanceof Error ? reason.message : String(reason)
      }
    }
    socket.onclose = () => {
      if (jobSockets.get(job.job_id) === socket) jobSockets.delete(job.job_id)
    }
  }
}

function isTerminal(status: string) {
  return ['completed', 'completed_with_errors', 'cancelled'].includes(status)
}

function jobPercent(job: KnowledgeJob) {
  return job.total_items ? Math.round((job.processed_items / job.total_items) * 100) : 100
}

function jobStatus(status: string) {
  return ({ queued: '排队中', running: '处理中', cancelling: '取消中', completed: '已完成', completed_with_errors: '部分失败', cancelled: '已取消' } as Record<string, string>)[status] || status
}

async function ingest() {
  const path = relativePath.value.trim()
  if (!selectedRoot.value || !path) return
  busy.value = { ...busy.value, ingest: true }
  error.value = ''
  try {
    await ingestKnowledgeSource(selectedRoot.value, path)
    relativePath.value = ''
    await refresh()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    const next = { ...busy.value }
    delete next.ingest
    busy.value = next
  }
}

async function decide(proposal: KnowledgeProposal, action: 'approve' | 'reject') {
  busy.value = { ...busy.value, [proposal.proposal_id]: true }
  error.value = ''
  try {
    await transitionKnowledgeProposal(proposal.proposal_id, action, proposal.revision)
    await refresh()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    const next = { ...busy.value }
    delete next[proposal.proposal_id]
    busy.value = next
  }
}

async function rollback(page: KnowledgePage, revisionId: string) {
  const key = `rollback:${page.page_id}`
  busy.value = { ...busy.value, [key]: true }
  error.value = ''
  try {
    await proposeKnowledgeRollback(page.page_id, revisionId, page.current_revision)
    await refresh()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    const next = { ...busy.value }
    delete next[key]
    busy.value = next
  }
}

onMounted(() => {
  void refresh()
  pollTimer = setInterval(() => void refreshJobs(), 2000)
})
onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
  for (const socket of jobSockets.values()) socket.close()
  jobSockets.clear()
})
</script>

<template>
  <AssistantSectionView
    eyebrow="Knowledge Workspace"
    title="知识库"
    description="来源保持不可变，Wiki 修改先生成 diff，批准和回滚都会形成新的 Git revision。"
    tone="source"
  >
    <p v-if="error" class="knowledge-error" role="alert">{{ error }}</p>
    <p v-if="loading" class="knowledge-state" role="status">正在读取知识空间...</p>

    <template v-else-if="summary">
      <section class="knowledge-metrics" aria-label="知识库状态">
        <div><strong>{{ summary.source_count }}</strong><span>来源</span></div>
        <div><strong>{{ summary.wiki_page_count }}</strong><span>Wiki 页面</span></div>
        <div><strong>{{ summary.pending_proposal_count }}</strong><span>待审核</span></div>
        <div><strong>{{ summary.workspace_name }}</strong><span>Git 工作区</span></div>
      </section>

      <section class="stage-content ingest-section">
        <div><h2>批量摄取目录</h2><p>{{ selectedSource?.label || '尚未配置来源' }} · 任务离开页面后仍会继续</p></div>
        <p v-if="!jobsAvailable" class="jobs-disabled" role="status">持久任务未启用。执行数据库迁移并设置 KNOWLEDGE_JOBS_ENABLED=true 后开放。</p>
        <form class="ingest-form" @submit.prevent="createBatch">
          <select v-model="selectedRoot" aria-label="批量来源目录" :disabled="busy.batch || !jobsAvailable">
            <option v-for="root in summary.source_roots" :key="root.root_id" :value="root.root_id">
              {{ root.label }} · {{ root.kind }}
            </option>
          </select>
          <input
            v-model="relativeDirectory"
            type="text"
            aria-label="来源相对目录"
            placeholder=". 或 03_项目/tourswarm"
            :disabled="busy.batch || !selectedRoot || !jobsAvailable"
          />
          <button type="submit" :disabled="busy.batch || !selectedRoot || !jobsAvailable">
            <FolderUp :size="16" />{{ busy.batch ? '正在扫描' : '创建持久任务' }}
          </button>
        </form>
      </section>

      <section class="knowledge-section jobs-section" aria-labelledby="jobs-title">
        <header><div><span>Durable ingestion</span><h2 id="jobs-title">批量任务</h2></div><strong>{{ jobs.length }}</strong></header>
        <p v-if="jobs.length === 0" class="empty-copy">尚无批量任务。目录会按 Markdown、HTML 和文本 PDF 拆分，失败项可单独重试。</p>
        <article v-for="job in jobs" :key="job.job_id" class="job-row">
          <div class="job-heading">
            <div><strong>{{ job.source_label }} / {{ job.relative_directory }}</strong><span>{{ jobStatus(job.status) }} · {{ job.processed_items }}/{{ job.total_items }}</span></div>
            <code>{{ job.job_id.slice(0, 18) }}</code>
          </div>
          <div class="job-progress" role="progressbar" :aria-valuenow="jobPercent(job)" aria-valuemin="0" aria-valuemax="100">
            <span :style="{ width: `${jobPercent(job)}%` }"></span>
          </div>
          <div class="job-counts">
            <span>成功 {{ job.succeeded_items }}</span><span>去重 {{ job.skipped_items }}</span><span>失败 {{ job.failed_items }}</span><span>取消 {{ job.cancelled_items }}</span>
            <button v-if="!isTerminal(job.status)" type="button" :disabled="busy[`cancel:${job.job_id}`]" @click="cancelJob(job)"><Square :size="13" />取消</button>
          </div>
          <ol v-if="job.items?.some((item) => item.status === 'dead_letter')" class="failed-items">
            <li v-for="item in job.items.filter((candidate) => candidate.status === 'dead_letter')" :key="item.item_id">
              <div><code>{{ item.relative_path }}</code><span>{{ item.error }}</span></div>
              <button type="button" :disabled="busy[`retry:${item.item_id}`]" @click="retryItem(job, item.item_id)"><RotateCw :size="13" />重试</button>
            </li>
          </ol>
        </article>
      </section>

      <section class="stage-content ingest-section single-ingest">
        <div><h2>单文件提案</h2><p>精确导入 Markdown、HTML 或文本 PDF</p></div>
        <form class="ingest-form" @submit.prevent="ingest">
          <select v-model="selectedRoot" aria-label="来源目录" :disabled="busy.ingest">
            <option v-for="root in summary.source_roots" :key="root.root_id" :value="root.root_id">
              {{ root.label }} · {{ root.kind }}
            </option>
          </select>
          <input
            v-model="relativePath"
            type="text"
            aria-label="来源相对路径"
            placeholder="例如：notes/harness.md、docs/guide.html 或 reports/design.pdf"
            :disabled="busy.ingest || !selectedRoot"
          />
          <button type="submit" :disabled="busy.ingest || !relativePath.trim() || !selectedRoot">
            <FilePlus2 :size="16" />{{ busy.ingest ? '正在生成提案' : '生成摄取提案' }}
          </button>
        </form>
      </section>

      <section class="knowledge-section" aria-labelledby="proposal-title">
        <header><div><span>Review queue</span><h2 id="proposal-title">待审核知识更新</h2></div><strong>{{ proposals.length }}</strong></header>
        <p v-if="proposals.length === 0" class="empty-copy">当前没有待审核提案。导入来源后，Wiki 不会立即变化。</p>
        <article v-for="proposal in proposals" :key="proposal.proposal_id" class="proposal-row">
          <div class="proposal-heading">
            <div><strong>{{ proposal.title }}</strong><span>{{ proposal.change_kind === 'rollback' ? '回滚提案' : proposal.source_relative_path }}</span></div>
            <code>{{ proposal.source_revision.slice(0, 22) }}</code>
          </div>
          <pre><code>{{ proposal.diff || '内容与当前页面一致' }}</code></pre>
          <div class="proposal-actions">
            <button type="button" class="reject" :disabled="busy[proposal.proposal_id]" @click="decide(proposal, 'reject')"><X :size="15" />拒绝</button>
            <button type="button" class="approve" :disabled="busy[proposal.proposal_id]" @click="decide(proposal, 'approve')"><Check :size="15" />批准并提交</button>
          </div>
        </article>
      </section>

      <section class="knowledge-section" aria-labelledby="pages-title">
        <header><div><span>Versioned wiki</span><h2 id="pages-title">已批准页面</h2></div><strong>{{ pages.length }}</strong></header>
        <p v-if="pages.length === 0" class="empty-copy">批准首个提案后，页面和 Git revision 会出现在这里。</p>
        <article v-for="page in pages" :key="page.page_id" class="page-row">
          <div><strong>{{ page.title }}</strong><code>{{ page.path }}</code></div>
          <ol>
            <li v-for="revision in [...page.revisions].reverse()" :key="revision.revision_id">
              <span>r{{ revision.sequence }} · {{ revision.change_kind }} · {{ revision.git_commit.slice(0, 8) }}</span>
              <button
                v-if="revision.revision_id !== page.current_revision"
                type="button"
                :disabled="busy[`rollback:${page.page_id}`]"
                @click="rollback(page, revision.revision_id)"
              ><RotateCcw :size="14" />生成回滚提案</button>
              <em v-else>当前</em>
            </li>
          </ol>
        </article>
      </section>
    </template>
  </AssistantSectionView>
</template>

<style scoped>
.jobs-disabled { margin:0 0 9px; color:var(--sage-coral); font-size:var(--sage-font-xs); }
.knowledge-error,.knowledge-state { margin:22px 0 0; padding:10px 12px; border-left:3px solid var(--sage-coral); background:var(--sage-danger-bg); color:var(--sage-text-secondary); font-size:var(--sage-font-sm); }.knowledge-state { border-color:var(--sage-source); background:var(--sage-source-bg); }.knowledge-metrics { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); border-bottom:1px solid var(--sage-border); }.knowledge-metrics div { min-width:0; padding:24px 18px; border-right:1px solid var(--sage-border); }.knowledge-metrics div:last-child { border-right:0; }.knowledge-metrics strong,.knowledge-metrics span { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }.knowledge-metrics strong { font-size:19px; }.knowledge-metrics span { margin-top:5px; color:var(--sage-text-muted); font-size:var(--sage-font-xs); }.ingest-section>div p { margin-top:5px; font-size:var(--sage-font-xs); }.single-ingest { border-top:1px solid var(--sage-border); }.ingest-form { display:grid; grid-template-columns:190px minmax(0,1fr) auto; gap:8px; }.ingest-form select,.ingest-form input { min-width:0; height:38px; border:1px solid var(--sage-border-strong); border-radius:var(--sage-radius-sm); background:var(--sage-surface); color:var(--sage-text); padding:0 10px; }.ingest-form button,.proposal-actions button,.page-row button,.job-row button { display:inline-flex; align-items:center; justify-content:center; gap:6px; min-height:34px; border:1px solid var(--sage-border-strong); border-radius:var(--sage-radius-sm); background:var(--sage-surface); color:var(--sage-text); padding:0 12px; font-weight:650; }.ingest-form button,.proposal-actions .approve { border-color:var(--sage-source); background:var(--sage-source); color:white; }.knowledge-section { padding:30px 0; border-bottom:1px solid var(--sage-border); }.knowledge-section>header { display:flex; align-items:end; justify-content:space-between; gap:16px; margin-bottom:18px; }.knowledge-section>header span { color:var(--sage-text-muted); font-size:var(--sage-font-xs); text-transform:uppercase; }.knowledge-section h2 { margin:5px 0 0; font-size:var(--sage-font-lg); }.knowledge-section>header>strong { color:var(--sage-source); font-size:22px; }.empty-copy { color:var(--sage-text-muted); }.proposal-row,.page-row,.job-row { padding:18px 0; border-top:1px solid var(--sage-border); }.proposal-heading,.job-heading { display:flex; justify-content:space-between; gap:18px; }.proposal-heading strong,.proposal-heading span,.page-row>div strong,.page-row>div code,.job-heading strong,.job-heading span { display:block; }.proposal-heading span,.proposal-heading code,.page-row code,.job-heading span,.job-heading code { margin-top:5px; color:var(--sage-text-muted); font-size:var(--sage-font-xs); }.job-progress { height:6px; margin:14px 0 10px; overflow:hidden; border-radius:3px; background:var(--sage-border); }.job-progress span { display:block; height:100%; background:var(--sage-source); transition:width .2s ease; }.job-counts { display:flex; align-items:center; gap:14px; color:var(--sage-text-muted); font-size:var(--sage-font-xs); }.job-counts button { margin-left:auto; min-height:28px; color:var(--sage-coral); }.failed-items { margin:12px 0 0; padding:0; list-style:none; }.failed-items li { display:flex; justify-content:space-between; align-items:center; gap:12px; padding:9px 0; border-top:1px dashed var(--sage-border); }.failed-items code,.failed-items span { display:block; }.failed-items span { margin-top:3px; color:var(--sage-coral); font-size:var(--sage-font-xs); }.proposal-row pre { max-height:320px; margin:14px 0; overflow:auto; border:1px solid var(--sage-border); border-radius:var(--sage-radius-sm); background:var(--sage-code-bg); color:var(--sage-code-text); padding:13px; font-size:12px; line-height:1.55; }.proposal-actions { display:flex; justify-content:flex-end; gap:8px; }.proposal-actions .reject { color:var(--sage-coral); }.page-row ol { margin:14px 0 0; padding:0; list-style:none; }.page-row li { display:flex; align-items:center; justify-content:space-between; gap:12px; min-height:38px; border-top:1px dashed var(--sage-border); color:var(--sage-text-secondary); font-size:var(--sage-font-sm); }.page-row em { color:var(--sage-success); font-style:normal; font-weight:650; }button:disabled,input:disabled,select:disabled { opacity:.5; cursor:not-allowed; }
@media (max-width:760px) { .knowledge-metrics { grid-template-columns:repeat(2,minmax(0,1fr)); }.knowledge-metrics div:nth-child(2) { border-right:0; }.ingest-form { grid-template-columns:1fr; }.proposal-heading,.job-heading { flex-direction:column; gap:4px; }.job-counts { flex-wrap:wrap; }.job-counts button { margin-left:0; }.failed-items li { align-items:flex-start; flex-direction:column; }.proposal-actions { justify-content:stretch; }.proposal-actions button { flex:1; }.page-row li { align-items:flex-start; flex-direction:column; padding:9px 0; } }
</style>
