<script setup lang="ts">
import {
  Check,
  ExternalLink,
  FileClock,
  Globe2,
  LoaderCircle,
  ShieldCheck,
  X,
} from 'lucide-vue-next'
import type {
  CodingKnowledgeSourceProposal,
  CodingKnowledgeSourceProposalDetail,
} from '../../types/api'

const props = withDefaults(defineProps<{
  proposals: readonly CodingKnowledgeSourceProposal[]
  details?: Readonly<Record<string, CodingKnowledgeSourceProposalDetail | undefined>>
  busy?: Readonly<Record<string, boolean | undefined>>
  detailBusy?: Readonly<Record<string, boolean | undefined>>
  error?: string
  compact?: boolean
}>(), {
  details: () => ({}),
  busy: () => ({}),
  detailBusy: () => ({}),
  error: '',
  compact: false,
})

const emit = defineEmits<{
  approve: [proposalId: string, revision: number]
  reject: [proposalId: string, revision: number]
  loadDetail: [proposalId: string]
}>()

function sourceHost(value: string) {
  try {
    return new URL(value).hostname
  } catch {
    return 'Web 来源'
  }
}

function shortHash(value: string) {
  return value ? `sha256:${value.slice(0, 12)}` : '等待内容指纹'
}

function eventLabel(value: string) {
  return ({
    proposal_created: '提案已创建',
    proposal_applying: '正在创建不可变快照',
    proposal_approved: '已批准入库',
    proposal_rejected: '已拒绝',
    knowledge_job_created: '已创建 Knowledge Job',
    proposal_apply_failed: '入库失败，可重新审阅',
  } as Record<string, string>)[value] ?? value
}

function formatTime(value: string) {
  if (!value) return '--'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '--'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(date)
}

function handleAuditToggle(event: Event, proposalId: string) {
  const details = event.currentTarget as HTMLDetailsElement | null
  if (details?.open && !props.details[proposalId] && !props.detailBusy[proposalId]) {
    emit('loadDetail', proposalId)
  }
}
</script>

<template>
  <section
    class="source-proposal-list"
    :class="{ compact }"
    aria-label="待确认知识来源"
  >
    <p v-if="error" class="source-error" role="alert">{{ error }}</p>
    <article
      v-for="proposal in proposals"
      :key="proposal.proposal_id"
      class="source-proposal-row"
    >
      <header>
        <span class="source-icon"><Globe2 :size="16" /></span>
        <span class="source-title">
          <strong>{{ proposal.title || '待确认 Web 来源' }}</strong>
          <a :href="proposal.canonical_url" target="_blank" rel="noreferrer">
            {{ sourceHost(proposal.canonical_url) }}<ExternalLink :size="11" />
          </a>
        </span>
        <em>{{ proposal.status === 'applying' ? '正在入库' : '待确认' }}</em>
      </header>

      <p class="source-reason">{{ proposal.reason || 'Sage 建议将本轮已引用证据保存为知识来源。' }}</p>

      <div class="source-receipt">
        <span><ShieldCheck :size="12" />revision {{ proposal.revision }}</span>
        <code>{{ shortHash(proposal.content_hash) }}</code>
        <code v-for="evidence in proposal.evidence_refs.slice(0, 2)" :key="evidence">{{ evidence }}</code>
      </div>

      <details class="source-audit" @toggle="handleAuditToggle($event, proposal.proposal_id)">
        <summary>
          <FileClock :size="13" />审阅记录
          <LoaderCircle v-if="detailBusy[proposal.proposal_id]" :size="12" class="spinning" />
        </summary>
        <ol v-if="details[proposal.proposal_id]?.events.length">
          <li v-for="item in details[proposal.proposal_id]?.events" :key="item.event_id">
            <span><strong>{{ eventLabel(item.event_type) }}</strong><small>{{ formatTime(item.created_at) }}</small></span>
            <code>revision {{ item.revision }}</code>
          </li>
        </ol>
        <p v-else>{{ detailBusy[proposal.proposal_id] ? '正在读取不可变审阅轨迹...' : '展开后读取审阅轨迹。' }}</p>
      </details>

      <footer>
        <button
          data-action="reject-source"
          type="button"
          :disabled="proposal.status !== 'pending' || Boolean(busy[proposal.proposal_id])"
          @click="emit('reject', proposal.proposal_id, proposal.revision)"
        ><X :size="14" />忽略来源</button>
        <button
          data-action="approve-source"
          type="button"
          :disabled="proposal.status !== 'pending' || Boolean(busy[proposal.proposal_id])"
          @click="emit('approve', proposal.proposal_id, proposal.revision)"
        ><Check :size="14" />批准入库</button>
      </footer>
    </article>
  </section>
</template>

<style scoped>
.source-proposal-list { display:grid; gap:10px; min-width:0; margin-top:12px; }.source-error { margin:0; padding:8px 9px; border-left:3px solid var(--sage-danger); color:var(--sage-danger); background:color-mix(in srgb,var(--sage-danger) 6%,var(--sage-surface)); font-size:10px; line-height:1.45; }.source-proposal-row { min-width:0; padding:11px 0 0; border-top:1px solid var(--sage-border); }.source-proposal-row>header { display:grid; grid-template-columns:30px minmax(0,1fr) auto; align-items:center; gap:8px; }.source-icon { display:grid; place-items:center; width:28px; height:28px; border:1px solid var(--sage-border); border-radius:var(--sage-radius); color:var(--sage-source); background:var(--sage-surface-raised); }.source-title { display:grid; min-width:0; }.source-title strong { overflow:hidden; color:var(--sage-text); font-size:11px; text-overflow:ellipsis; white-space:nowrap; }.source-title a { display:flex; align-items:center; gap:3px; width:max-content; max-width:100%; overflow:hidden; margin-top:2px; color:var(--sage-source); font-size:9px; text-decoration:none; text-overflow:ellipsis; white-space:nowrap; }.source-title a:hover { text-decoration:underline; }.source-proposal-row>header em { color:var(--sage-warning); font-size:9px; font-style:normal; }.source-reason { margin:9px 0 0; color:var(--sage-text-secondary); font-size:10px; line-height:1.5; }.source-receipt { display:flex; flex-wrap:wrap; gap:5px; margin-top:8px; }.source-receipt span,.source-receipt code { display:inline-flex; align-items:center; gap:3px; padding:3px 5px; border:1px solid var(--sage-border); border-radius:var(--sage-radius-sm); color:var(--sage-text-muted); font-size:8px; }.source-audit { margin-top:8px; border-top:1px solid var(--sage-border); }.source-audit summary { display:flex; align-items:center; gap:5px; min-height:30px; color:var(--sage-text-muted); font-size:9px; cursor:pointer; list-style:none; }.source-audit summary::-webkit-details-marker { display:none; }.source-audit .spinning { margin-left:auto; animation:source-spin 1s linear infinite; }.source-audit ol { margin:0; padding:0; list-style:none; }.source-audit li { display:flex; align-items:center; justify-content:space-between; gap:8px; min-height:32px; border-top:1px solid var(--sage-border); }.source-audit li span { display:grid; }.source-audit li strong { color:var(--sage-text-secondary); font-size:9px; }.source-audit li small,.source-audit li code,.source-audit>p { color:var(--sage-text-muted); font-size:8px; }.source-audit>p { margin:0; padding:7px 0; }.source-proposal-row>footer { display:flex; justify-content:flex-end; gap:6px; margin-top:9px; }.source-proposal-row button { display:inline-flex; align-items:center; gap:4px; min-height:30px; padding:0 9px; border:1px solid var(--sage-border); border-radius:var(--sage-radius); color:var(--sage-text-secondary); background:var(--sage-surface); font-size:10px; font-weight:650; }.source-proposal-row button[data-action="approve-source"] { border-color:var(--sage-brand-strong); color:var(--sage-surface); background:var(--sage-brand-strong); }.source-proposal-row button:disabled { cursor:not-allowed; opacity:.55; }.source-proposal-list.compact { gap:7px; margin-top:0; }.compact .source-proposal-row { padding:10px 12px 11px; border-top:0; border-bottom:1px solid var(--sage-border); }.compact .source-proposal-row:last-child { border-bottom:0; }.compact .source-reason { display:-webkit-box; overflow:hidden; -webkit-box-orient:vertical; -webkit-line-clamp:2; }.compact .source-receipt code:nth-of-type(n+2) { display:none; }
@keyframes source-spin { to { transform:rotate(360deg); } }
</style>
