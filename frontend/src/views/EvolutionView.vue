<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  ArrowRight,
  BookOpenText,
  CheckCircle2,
  Circle,
  Clock3,
  FilePenLine,
  Globe2,
  Milestone,
  ShieldCheck,
  Sparkles,
} from 'lucide-vue-next'
import { fetchKnowledgeGraphInsights } from '../api/knowledge'
import { useAssistantHomeStore } from '../stores/assistantHome'
import type { KnowledgeGraphInsights } from '../types/api'

const home = useAssistantHomeStore()
const knowledgeInsights = ref<KnowledgeGraphInsights | null>(null)
const pendingCount = computed(() => {
  const proposals = home.summary?.proposals
  return proposals ? proposals.memory_pending + proposals.wiki_pending + proposals.note_pending : 0
})
const goalTitle = computed(() => knowledgeInsights.value?.goal.title || '在主对话中设定你的长期目标')
const goalDescription = computed(() => knowledgeInsights.value?.goal.description || '目标确认后，Sage 才会把知识、实践与作品组织成可追溯的成长路径。')
const capabilityPath = computed(() => {
  const snapshot = knowledgeInsights.value
  if (!snapshot) return []
  return snapshot.goal.capabilities.slice(0, 4).map((capability) => {
    const alignment = snapshot.alignments.find((item) => item.capability_id === capability.capability_id)
    const status = alignment?.status ?? 'gap'
    const matched = alignment?.matched_keywords.length ?? 0
    return {
      id: capability.capability_id,
      label: capability.label,
      status,
      detail: status === 'covered'
        ? `${matched} 个关键词已有知识证据`
        : status === 'learning'
          ? `${matched} 个关键词已有覆盖，仍需验证`
          : '等待知识与实践证据',
    }
  })
})

onMounted(() => {
  void home.load()
  void fetchKnowledgeGraphInsights()
    .then((result) => { knowledgeInsights.value = result })
    .catch(() => { knowledgeInsights.value = null })
})
</script>

<template>
  <div class="growth-view">
    <header class="growth-header">
      <div><h1>成长记录</h1><p>从目标、知识到作品，保留每一次可验证的进步。</p></div>
      <div><RouterLink class="secondary-action" to="/public" target="_blank"><Globe2 :size="15" />外部门面</RouterLink><RouterLink class="primary-action" to="/publishing"><FilePenLine :size="15" />写文章</RouterLink></div>
    </header>

    <main>
      <section class="growth-profile" aria-labelledby="growth-goal">
        <div class="profile-mark"><Sparkles :size="22" /></div>
        <div><span>当前方向</span><h2 id="growth-goal">{{ goalTitle }}</h2><p>{{ goalDescription }}</p></div>
        <dl><div><dt>知识资产</dt><dd>{{ home.summary?.knowledge.wiki_page_count ?? '—' }}</dd></div><div><dt>待确认沉淀</dt><dd>{{ pendingCount }}</dd></div><div><dt>对话线程</dt><dd>{{ home.summary?.sessions.total ?? '—' }}</dd></div></dl>
      </section>

      <section class="growth-section" aria-labelledby="path-title">
        <header><div><span>Learning path</span><h2 id="path-title">能力成长路径</h2></div><RouterLink to="/coding">回到主对话<ArrowRight :size="15" /></RouterLink></header>
        <ol v-if="capabilityPath.length" class="growth-path">
          <li v-for="capability in capabilityPath" :key="capability.id" :class="{ completed: capability.status === 'covered', current: capability.status === 'learning' }">
            <span><CheckCircle2 v-if="capability.status === 'covered'" :size="17" /><Milestone v-else-if="capability.status === 'learning'" :size="17" /><Circle v-else :size="17" /></span>
            <div><time>{{ capability.status === 'covered' ? '已有证据' : capability.status === 'learning' ? '正在建立' : '尚待覆盖' }}</time><strong>{{ capability.label }}</strong><p>{{ capability.detail }}</p></div>
          </li>
        </ol>
        <div v-else class="growth-path-empty"><Clock3 :size="17" /><span><strong>等待目标能力投影</strong><small>这里只接受 Goal 与 Timeline 的真实证据，不推测掌握度。</small></span></div>
      </section>

      <div class="growth-columns">
        <section class="growth-section" aria-labelledby="activity-title">
          <header><div><span>Current status</span><h2 id="activity-title">最近状态</h2></div></header>
          <div class="growth-list">
            <article><BookOpenText :size="17" /><span><strong>{{ home.summary?.knowledge.wiki_page_count ?? 0 }} 个 Wiki 页面可用于目标检索</strong><small>来自 Knowledge workspace summary</small></span><time>当前</time></article>
            <article><ShieldCheck :size="17" /><span><strong>{{ pendingCount ? `${pendingCount} 条沉淀等待确认` : '当前没有待确认沉淀' }}</strong><small>只有用户批准后才会写入知识库</small></span><time>{{ pendingCount ? '待处理' : '已清' }}</time></article>
            <article><Clock3 :size="17" /><span><strong>{{ home.summary?.sessions.total ?? 0 }} 条主对话线程可继续</strong><small>目标跟进与任务执行保留在主对话</small></span><time>当前</time></article>
          </div>
        </section>

        <section class="publishing-section" aria-labelledby="publishing-title">
          <div><span>Publishing</span><h2 id="publishing-title">把真实成长变成可阅读作品</h2><p>主对话或 Codex 生成草稿，在发布工作室完成编辑、证据检查与最终确认。</p></div>
          <RouterLink to="/publishing">打开发布工作室<ArrowRight :size="15" /></RouterLink>
        </section>
      </div>
    </main>
  </div>
</template>

<style scoped>
.growth-view { width:min(1120px,100%); margin:0 auto; padding:42px 38px 64px; }.growth-header { display:flex; align-items:flex-end; justify-content:space-between; gap:24px; padding-bottom:26px; border-bottom:1px solid var(--sage-border); }.growth-header h1 { margin:0; font-size:var(--sage-font-title); }.growth-header p { margin:6px 0 0; color:var(--sage-text-muted); }.growth-header>div:last-child { display:flex; gap:8px; }.growth-header a,.growth-section header a,.publishing-section a { display:inline-flex; align-items:center; gap:6px; color:inherit; text-decoration:none; }.primary-action,.secondary-action { min-height:36px; padding:0 12px; border:1px solid var(--sage-border); border-radius:var(--sage-radius); font-size:var(--sage-font-sm); }.primary-action { color:white !important; border-color:var(--sage-brand-strong); background:var(--sage-brand-strong); }
.growth-profile { display:grid; grid-template-columns:auto minmax(0,1fr) auto; align-items:center; gap:18px; padding:34px 0; border-bottom:1px solid var(--sage-border); }.profile-mark { display:grid; place-items:center; width:48px; height:48px; border-radius:50%; color:white; background:var(--sage-brand-strong); }.growth-profile span,.growth-section header span,.publishing-section span { color:var(--sage-text-muted); font-size:var(--sage-font-xs); }.growth-profile h2 { margin:4px 0 5px; font-size:22px; }.growth-profile p { margin:0; color:var(--sage-text-muted); }.growth-profile dl { display:flex; margin:0; }.growth-profile dl>div { display:grid; min-width:96px; padding-left:18px; border-left:1px solid var(--sage-border); }.growth-profile dt { color:var(--sage-text-muted); font-size:var(--sage-font-xs); }.growth-profile dd { margin:4px 0 0; font-size:21px; font-weight:700; }
.growth-section { padding:30px 0; }.growth-section>header { display:flex; align-items:flex-end; justify-content:space-between; gap:16px; margin-bottom:20px; }.growth-section h2,.publishing-section h2 { margin:3px 0 0; font-size:19px; }.growth-section header a { color:var(--sage-brand-strong); font-size:var(--sage-font-sm); font-weight:650; }.growth-path { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); margin:0; padding:0; list-style:none; }.growth-path li { position:relative; display:grid; grid-template-columns:34px minmax(0,1fr); gap:9px; padding-right:22px; }.growth-path li::after { position:absolute; top:16px; left:34px; right:0; height:1px; background:var(--sage-border); content:''; }.growth-path li>span { position:relative; z-index:2; display:grid; place-items:center; width:34px; height:34px; border:1px solid var(--sage-border-strong); border-radius:50%; color:var(--sage-text-muted); background:var(--sage-surface); }.growth-path li.completed>span { color:white; border-color:var(--sage-brand); background:var(--sage-brand); }.growth-path li.current>span { color:var(--sage-brand-strong); border-color:var(--sage-brand); box-shadow:0 0 0 4px var(--sage-brand-bg); }.growth-path time,.growth-path strong,.growth-path p { display:block; }.growth-path time { color:var(--sage-text-muted); font-size:10px; }.growth-path strong { margin-top:6px; font-size:var(--sage-font-sm); }.growth-path p { margin:4px 0 0; color:var(--sage-text-muted); font-size:var(--sage-font-xs); line-height:1.5; }.growth-path-empty { display:flex; align-items:center; gap:10px; min-height:74px; padding:0 14px; border-top:1px solid var(--sage-border); border-bottom:1px solid var(--sage-border); color:var(--sage-text-muted); }.growth-path-empty span { display:grid; }.growth-path-empty strong { color:var(--sage-text-secondary); font-size:var(--sage-font-sm); }.growth-path-empty small { margin-top:3px; font-size:var(--sage-font-xs); }
.growth-columns { display:grid; grid-template-columns:minmax(0,1.35fr) minmax(280px,.65fr); gap:48px; border-top:1px solid var(--sage-border); }.growth-list { border-top:1px solid var(--sage-border); }.growth-list article { display:grid; grid-template-columns:22px minmax(0,1fr) auto; align-items:center; gap:10px; min-height:64px; border-bottom:1px solid var(--sage-border); }.growth-list article>svg { color:var(--sage-brand); }.growth-list span { display:grid; }.growth-list strong { font-size:var(--sage-font-sm); }.growth-list small,.growth-list time { color:var(--sage-text-muted); font-size:var(--sage-font-xs); }.publishing-section { align-self:start; margin-top:30px; padding:22px 0; border-top:3px solid var(--sage-brand); border-bottom:1px solid var(--sage-border); }.publishing-section p { color:var(--sage-text-muted); font-size:var(--sage-font-sm); }.publishing-section a { margin-top:10px; color:var(--sage-brand-strong); font-size:var(--sage-font-sm); font-weight:650; }
@media (max-width:900px) { .growth-view { padding-top:70px; }.growth-profile { grid-template-columns:auto minmax(0,1fr); }.growth-profile dl { grid-column:1/-1; }.growth-profile dl>div:first-child { padding-left:0; border-left:0; }.growth-columns { grid-template-columns:1fr; gap:0; } }
@media (max-width:650px) { .growth-view { padding-right:18px; padding-left:18px; }.growth-header { align-items:flex-start; }.growth-header p,.secondary-action { display:none; }.growth-header h1 { font-size:24px; }.growth-profile { padding:25px 0; }.growth-profile h2 { font-size:19px; }.growth-profile dl { width:100%; }.growth-profile dl>div { flex:1; min-width:0; }.growth-path { grid-template-columns:1fr; gap:22px; }.growth-path li { min-height:74px; padding-right:0; }.growth-path li::after { top:34px; bottom:-22px; left:16px; right:auto; width:1px; height:auto; }.growth-path li:last-child::after { display:none; } }
</style>
