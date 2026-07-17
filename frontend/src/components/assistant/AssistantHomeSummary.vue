<script setup lang="ts">
import { ArrowRight, BookOpenText, FolderGit2, History, Sprout } from 'lucide-vue-next'
import type { AssistantHomeSummary } from '../../types/api'

defineProps<{ summary: AssistantHomeSummary }>()
</script>

<template>
  <section class="action-section" aria-labelledby="suggested-title">
    <div class="section-heading"><div><span>下一步</span><h2 id="suggested-title">从当前状态继续</h2></div></div>
    <div class="action-list">
      <RouterLink v-for="action in summary.suggested_actions" :key="action.id" :to="action.target" :data-kind="action.kind">
        <span><strong>{{ action.label }}</strong><small>{{ action.description }}</small></span><ArrowRight :size="16" />
      </RouterLink>
    </div>
  </section>

  <div class="home-columns">
    <section aria-labelledby="sessions-title">
      <div class="section-heading"><div><span>最近</span><h2 id="sessions-title"><History :size="16" />对话</h2></div><b>{{ summary.sessions.total }}</b></div>
      <p v-if="summary.sessions.error" class="section-error" role="status">{{ summary.sessions.error }}</p>
      <div v-else-if="summary.sessions.items.length" class="plain-list">
        <RouterLink v-for="session in summary.sessions.items" :key="session.session_id" :to="session.target">
          <span><strong>{{ session.title }}</strong><small>{{ session.workspace_name }} · {{ session.message_count }} 条消息</small></span><ArrowRight :size="15" />
        </RouterLink>
      </div>
      <p v-else class="section-empty">还没有可继续的对话。</p>
    </section>

    <section aria-labelledby="projects-title">
      <div class="section-heading"><div><span>工作区</span><h2 id="projects-title"><FolderGit2 :size="16" />项目</h2></div><b>{{ summary.projects.total }}</b></div>
      <p v-if="summary.projects.error" class="section-error" role="status">{{ summary.projects.error }}</p>
      <div v-else-if="summary.projects.items.length" class="plain-list">
        <div v-for="project in summary.projects.items" :key="project.project_id" class="static-row">
          <span><strong>{{ project.name }}</strong><small>GitHub 项目</small></span>
        </div>
      </div>
      <p v-else class="section-empty">{{ summary.projects.status === 'unavailable' ? '本地模式暂未绑定云项目。' : '还没有云项目。' }}</p>
    </section>
  </div>

  <section class="evolution-band" aria-labelledby="evolution-title">
    <div><span class="band-icon"><Sprout :size="18" /></span><span><strong id="evolution-title">待确认沉淀</strong><small>记忆 {{ summary.proposals.memory_pending }} · Wiki {{ summary.proposals.wiki_pending }} · 笔记 {{ summary.proposals.note_pending }}</small></span></div>
    <RouterLink to="/evolution">查看成长记录<ArrowRight :size="15" /></RouterLink>
  </section>

  <section class="knowledge-band" aria-labelledby="knowledge-title">
    <BookOpenText :size="18" /><span><strong id="knowledge-title">知识空间尚未配置</strong><small>V7-P2 将接入 Markdown、Obsidian 与 GitHub，并生成可审核的持久 Wiki。</small></span>
    <RouterLink to="/knowledge">查看边界</RouterLink>
  </section>
</template>

<style scoped>
section { min-width:0; }.section-heading { display:flex; align-items:flex-end; justify-content:space-between; gap:16px; margin-bottom:10px; }.section-heading > div { display:flex; flex-direction:column; gap:3px; }.section-heading span { color:var(--sage-text-muted); font-size:var(--sage-font-xs); text-transform:uppercase; }.section-heading h2 { display:flex; align-items:center; gap:7px; margin:0; font-size:var(--sage-font-lg); letter-spacing:0; }.section-heading b { color:var(--sage-text-muted); font-size:var(--sage-font-sm); }
.action-section { margin-top:26px; padding-top:20px; border-top:1px solid var(--sage-border); }.action-list { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }.action-list a { display:flex; align-items:center; justify-content:space-between; gap:14px; min-height:66px; padding:11px 13px; border:1px solid var(--sage-border); border-left:3px solid var(--sage-source); border-radius:var(--sage-radius); color:var(--sage-text); background:var(--sage-surface); text-decoration:none; }.action-list a[data-kind="knowledge"] { border-left-color:var(--sage-brand); }.action-list a[data-kind="review"] { border-left-color:var(--sage-review); }.action-list a:hover { border-color:var(--sage-border-strong); background:var(--sage-surface-raised); }.action-list span,.plain-list span,.static-row span { display:flex; flex-direction:column; min-width:0; gap:3px; }.action-list strong,.plain-list strong,.static-row strong { overflow:hidden; font-size:var(--sage-font-md); text-overflow:ellipsis; white-space:nowrap; }.action-list small,.plain-list small,.static-row small { overflow:hidden; color:var(--sage-text-muted); font-size:var(--sage-font-xs); text-overflow:ellipsis; white-space:nowrap; }
.home-columns { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:34px; margin-top:30px; }.plain-list { border-top:1px solid var(--sage-border); }.plain-list a,.static-row { display:flex; align-items:center; justify-content:space-between; gap:12px; min-height:54px; padding:8px 3px; border-bottom:1px solid var(--sage-border); color:var(--sage-text); text-decoration:none; }.plain-list a:hover strong { color:var(--sage-source); }.section-empty,.section-error { min-height:56px; margin:0; padding:15px 3px; border-top:1px solid var(--sage-border); border-bottom:1px solid var(--sage-border); color:var(--sage-text-muted); font-size:var(--sage-font-sm); }.section-error { color:var(--sage-danger); }
.evolution-band,.knowledge-band { display:flex; align-items:center; justify-content:space-between; gap:16px; margin-top:28px; padding:14px 0; border-top:1px solid var(--sage-border); border-bottom:1px solid var(--sage-border); }.evolution-band > div,.knowledge-band > span { display:flex; align-items:center; gap:10px; min-width:0; }.evolution-band > div > span:last-child,.knowledge-band > span { flex-direction:column; align-items:flex-start; gap:3px; }.band-icon { display:grid; place-items:center; width:36px; height:36px; flex:none; border-radius:var(--sage-radius); color:var(--sage-review-strong); background:var(--sage-review-bg); }.evolution-band strong,.knowledge-band strong { font-size:var(--sage-font-md); }.evolution-band small,.knowledge-band small { color:var(--sage-text-muted); font-size:var(--sage-font-xs); }.evolution-band a,.knowledge-band a { display:flex; align-items:center; gap:5px; flex:none; color:var(--sage-source); font-size:var(--sage-font-sm); font-weight:650; text-decoration:none; }.knowledge-band > svg { flex:none; color:var(--sage-brand); }
@media (max-width:720px) { .action-list,.home-columns { grid-template-columns:1fr; }.home-columns { gap:26px; }.knowledge-band { align-items:flex-start; }.knowledge-band a { display:none; } }
</style>
