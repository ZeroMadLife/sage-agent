<script setup lang="ts">
import {
  AlertCircle,
  Check,
  CheckCircle2,
  Clock3,
  FileSearch,
  FlaskConical,
  GitBranch,
  NotebookPen,
  X,
} from 'lucide-vue-next'
import type { HarnessReviewBundle } from '../../harness/reviewBundle'

const props = withDefaults(defineProps<{
  bundle: HarnessReviewBundle
  depositBusy?: boolean
}>(), {
  depositBusy: false,
})

const emit = defineEmits<{
  approveDeposit: [proposalId: string, revision: number]
  rejectDeposit: [proposalId: string, revision: number]
}>()

function durationLabel(durationMs?: number) {
  if (durationMs === undefined) return ''
  if (durationMs < 1_000) return `${durationMs}ms`
  if (durationMs < 60_000) return `${(durationMs / 1_000).toFixed(durationMs < 10_000 ? 1 : 0)}s`
  return `${Math.floor(durationMs / 60_000)}m ${Math.round(durationMs % 60_000 / 1_000)}s`
}
</script>

<template>
  <section class="review-bundle" aria-labelledby="review-bundle-title">
    <header class="review-bundle-heading">
      <div>
        <span>Review Bundle</span>
        <h2 id="review-bundle-title">本轮成果与沉淀</h2>
      </div>
      <code>{{ bundle.runId || '等待运行' }}</code>
    </header>

    <div class="review-sections">
      <section class="review-section review-evidence" :data-status="bundle.evidence.status">
        <header>
          <span class="review-icon"><FileSearch :size="17" /></span>
          <div><h3>证据包</h3><small>{{ bundle.evidence.items.length }} 条可追溯证据</small></div>
          <CheckCircle2 v-if="bundle.evidence.status === 'ready'" :size="16" class="status-icon" />
          <Clock3 v-else-if="bundle.evidence.status === 'running'" :size="16" class="status-icon" />
          <AlertCircle v-else-if="bundle.evidence.status === 'failed'" :size="16" class="status-icon" />
        </header>
        <div v-if="bundle.evidence.items.length" class="evidence-rows">
          <article v-for="item in bundle.evidence.items.slice(0, 3)" :key="item.id" :title="item.excerpt">
            <span><strong>{{ item.title }}</strong><small>{{ item.source }}</small></span>
            <code>{{ item.pageRevision.slice(0, 14) }}</code>
          </article>
          <p v-if="bundle.evidence.items.length > 3" class="more-row">
            另有 {{ bundle.evidence.items.length - 3 }} 条证据保留在对话工具记录中
          </p>
        </div>
        <p v-else class="review-empty">
          {{ bundle.evidence.status === 'running' ? '正在检索与核对引用证据。' : bundle.evidence.status === 'failed' ? '知识检索失败，未形成可引用证据。' : '本轮尚无带 revision 的检索证据。' }}
        </p>
      </section>

      <section class="review-section review-practice" :data-status="bundle.practice.status">
        <header>
          <span class="review-icon"><FlaskConical :size="17" /></span>
          <div><h3>实践结果</h3><small>只汇总真实工具与运行终态</small></div>
          <CheckCircle2 v-if="bundle.practice.status === 'complete'" :size="16" class="status-icon" />
          <Clock3 v-else-if="bundle.practice.status === 'running'" :size="16" class="status-icon" />
          <AlertCircle v-else-if="bundle.practice.status === 'failed'" :size="16" class="status-icon" />
        </header>
        <template v-if="bundle.practice.status !== 'empty'">
          <p class="practice-headline">{{ bundle.practice.headline }}</p>
          <div class="practice-metrics">
            <span>{{ bundle.practice.toolCount }} 项工具</span>
            <span v-if="bundle.practice.failedToolCount" class="failed">{{ bundle.practice.failedToolCount }} 项失败</span>
            <span v-if="bundle.practice.approvalCount">{{ bundle.practice.approvalCount }} 次审批</span>
            <span v-if="durationLabel(bundle.practice.durationMs)">{{ durationLabel(bundle.practice.durationMs) }}</span>
          </div>
          <p v-if="bundle.practice.changedFiles.length" class="changed-files">
            <GitBranch :size="13" />{{ bundle.practice.changedFiles.slice(0, 2).join('、') }}<template v-if="bundle.practice.changedFiles.length > 2"> 等 {{ bundle.practice.changedFiles.length }} 个文件</template>
          </p>
        </template>
        <p v-else class="review-empty">运行尚未产生可验证的实践结果。</p>
      </section>

      <section class="review-section review-deposit" :data-status="bundle.deposit.status">
        <header>
          <span class="review-icon"><NotebookPen :size="17" /></span>
          <div><h3>沉淀提案</h3><small>确认后才进入长期记忆</small></div>
          <AlertCircle v-if="bundle.deposit.status === 'review'" :size="16" class="status-icon" />
        </header>
        <template v-if="bundle.deposit.status === 'review'">
          <p class="deposit-copy">{{ bundle.deposit.items[0] || '提案已生成，等待查看。' }}</p>
          <small v-if="bundle.deposit.items.length > 1" class="deposit-more">共 {{ bundle.deposit.items.length }} 条候选记忆</small>
          <footer>
            <button data-action="reject" type="button" :disabled="props.depositBusy" @click="emit('rejectDeposit', bundle.deposit.proposalId, bundle.deposit.revision)"><X :size="14" />拒绝</button>
            <button data-action="approve" type="button" :disabled="props.depositBusy" @click="emit('approveDeposit', bundle.deposit.proposalId, bundle.deposit.revision)"><Check :size="14" />批准沉淀</button>
          </footer>
        </template>
        <p v-else class="review-empty">本轮尚无待确认的 Wiki 或 Memory 增量。</p>
      </section>
    </div>
  </section>
</template>

<style scoped>
.review-bundle { margin-top:24px; border:1px solid var(--sage-border); border-radius:var(--sage-radius); overflow:hidden; background:var(--sage-surface); }
.review-bundle-heading { display:flex; align-items:center; justify-content:space-between; gap:12px; min-height:54px; padding:9px 13px; border-bottom:1px solid var(--sage-border); background:var(--sage-surface-muted); }.review-bundle-heading>div { min-width:0; }.review-bundle-heading span { color:var(--sage-brand-strong); font-size:9px; font-weight:750; text-transform:uppercase; }.review-bundle-heading h2 { margin:2px 0 0; font-size:var(--sage-font-md); }.review-bundle-heading code { max-width:40%; overflow:hidden; color:var(--sage-text-muted); font-size:10px; text-overflow:ellipsis; white-space:nowrap; }
.review-sections { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); }.review-section { min-width:0; min-height:196px; padding:13px; border-right:1px solid var(--sage-border); }.review-section:last-child { border-right:0; }.review-section>header { display:grid; grid-template-columns:32px minmax(0,1fr) 18px; align-items:center; gap:8px; }.review-icon { display:grid; place-items:center; width:30px; height:30px; border:1px solid var(--sage-border); border-radius:var(--sage-radius); color:var(--sage-text-secondary); background:var(--sage-surface-raised); }.review-section h3 { margin:0; font-size:var(--sage-font-sm); }.review-section header small { display:block; margin-top:2px; color:var(--sage-text-muted); font-size:9px; }.status-icon { color:var(--sage-success); }.review-section[data-status="running"] .status-icon,.review-section[data-status="review"] .status-icon { color:var(--sage-warning); }.review-section[data-status="failed"] .status-icon { color:var(--sage-danger); }
.evidence-rows { margin-top:10px; }.evidence-rows article { display:flex; align-items:center; justify-content:space-between; gap:9px; min-height:38px; border-top:1px solid var(--sage-border); }.evidence-rows article>span,.evidence-rows strong,.evidence-rows small { display:block; min-width:0; }.evidence-rows strong { overflow:hidden; font-size:11px; text-overflow:ellipsis; white-space:nowrap; }.evidence-rows small { overflow:hidden; margin-top:2px; color:var(--sage-text-muted); font-size:9px; text-overflow:ellipsis; white-space:nowrap; }.evidence-rows code { flex:none; color:var(--sage-source); font-size:9px; }.more-row,.review-empty { margin:11px 0 0; color:var(--sage-text-muted); font-size:10px; line-height:1.55; }.more-row { padding-top:7px; border-top:1px solid var(--sage-border); }
.practice-headline,.deposit-copy { margin:13px 0 0; color:var(--sage-text-secondary); font-size:11px; line-height:1.5; }.practice-metrics { display:flex; flex-wrap:wrap; gap:5px; margin-top:10px; }.practice-metrics span { padding:3px 6px; border:1px solid var(--sage-border); border-radius:var(--sage-radius-sm); color:var(--sage-text-muted); font-size:9px; }.practice-metrics span.failed { color:var(--sage-danger); }.changed-files { display:flex; align-items:flex-start; gap:5px; margin:10px 0 0; color:var(--sage-text-muted); font-size:9px; line-height:1.45; overflow-wrap:anywhere; }.changed-files svg { flex:none; margin-top:1px; }
.deposit-more { display:block; margin-top:6px; color:var(--sage-text-muted); font-size:9px; }.review-deposit footer { display:flex; justify-content:flex-end; gap:6px; margin-top:13px; }.review-deposit button { display:inline-flex; align-items:center; gap:4px; min-height:30px; padding:0 9px; border:1px solid var(--sage-border); border-radius:var(--sage-radius); color:var(--sage-text-secondary); background:var(--sage-surface); font-size:10px; font-weight:650; }.review-deposit button[data-action="approve"] { border-color:var(--sage-brand-strong); color:var(--sage-surface); background:var(--sage-brand-strong); }.review-deposit button:disabled { cursor:not-allowed; opacity:.55; }
@media (max-width:1050px) { .review-sections { grid-template-columns:1fr; }.review-section { min-height:0; border-right:0; border-bottom:1px solid var(--sage-border); }.review-section:last-child { border-bottom:0; } }
</style>
