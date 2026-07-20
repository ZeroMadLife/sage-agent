<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import {
  ArrowLeft,
  Bold,
  Check,
  Code2,
  Eye,
  Heading2,
  Link2,
  ListChecks,
  Quote,
  Save,
  ShieldCheck,
  Sparkles,
} from 'lucide-vue-next'
import { useMarkdown } from '../composables/useMarkdown'
import {
  draftWordCount,
  loadPublishingDraft,
  savePublishingDraft,
  type PublishingDraft,
} from '../harness/publishingDraft'

const draft = ref<PublishingDraft>(loadPublishingDraft())
const bodyInput = ref<HTMLTextAreaElement | null>(null)
const previewing = ref(false)
const savedLabel = ref('本地草稿')
const { render } = useMarkdown()
const renderedBody = computed(() => render(draft.value.body))
const wordCount = computed(() => draftWordCount(draft.value.body))
let saveTimer: ReturnType<typeof setTimeout> | null = null

function save() {
  draft.value = savePublishingDraft(draft.value)
  savedLabel.value = '已保存'
}

watch(draft, () => {
  savedLabel.value = '保存中'
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(save, 450)
}, { deep: true })

onBeforeUnmount(() => {
  if (saveTimer) clearTimeout(saveTimer)
})

function wrapSelection(prefix: string, suffix = prefix, placeholder = '文字') {
  const input = bodyInput.value
  if (!input) return
  const start = input.selectionStart
  const end = input.selectionEnd
  const selected = draft.value.body.slice(start, end) || placeholder
  draft.value.body = `${draft.value.body.slice(0, start)}${prefix}${selected}${suffix}${draft.value.body.slice(end)}`
  void nextTick(() => {
    input.focus()
    input.setSelectionRange(start + prefix.length, start + prefix.length + selected.length)
  })
}
</script>

<template>
  <div class="publishing-studio">
    <header class="studio-header">
      <div>
        <RouterLink to="/growth" aria-label="返回成长记录" title="返回成长记录"><ArrowLeft :size="18" /></RouterLink>
        <span><strong>发布工作室</strong><small>{{ savedLabel }}</small></span>
      </div>
      <div class="studio-actions">
        <button type="button" class="preview-button" :aria-pressed="previewing" @click="previewing = !previewing"><Eye :size="15" />{{ previewing ? '继续编辑' : '预览' }}</button>
        <RouterLink class="external-preview" to="/public?preview=draft" target="_blank" rel="noopener" @click="save"><Eye :size="15" />外部预览</RouterLink>
        <button type="button" class="publish-button" disabled title="等待后端发布 API"><Save :size="15" />发布</button>
      </div>
    </header>

    <main class="studio-main">
      <article class="editor-sheet">
        <template v-if="!previewing">
          <input v-model="draft.title" class="title-input" aria-label="文章标题" placeholder="文章标题" />
          <textarea v-model="draft.summary" class="summary-input" rows="2" aria-label="文章摘要" placeholder="用一句话说明这篇内容的价值"></textarea>
          <div class="editor-toolbar" role="toolbar" aria-label="Markdown 格式">
            <button type="button" title="二级标题" aria-label="二级标题" @click="wrapSelection('## ', '', '标题')"><Heading2 :size="17" /></button>
            <button type="button" title="加粗" aria-label="加粗" @click="wrapSelection('**', '**')"><Bold :size="17" /></button>
            <button type="button" title="引用" aria-label="引用" @click="wrapSelection('> ', '', '引用内容')"><Quote :size="17" /></button>
            <button type="button" title="行内代码" aria-label="行内代码" @click="wrapSelection('`', '`', 'code')"><Code2 :size="17" /></button>
            <button type="button" title="链接" aria-label="链接" @click="wrapSelection('[', '](https://)', '链接文字')"><Link2 :size="17" /></button>
          </div>
          <textarea ref="bodyInput" v-model="draft.body" class="body-input" aria-label="Markdown 正文" spellcheck="true"></textarea>
        </template>
        <template v-else>
          <header class="preview-heading"><h1>{{ draft.title || '无标题草稿' }}</h1><p>{{ draft.summary }}</p></header>
          <div class="markdown-preview message-content" v-html="renderedBody"></div>
        </template>

        <footer class="editor-footer">
          <span>{{ wordCount }} 字</span>
          <span><Check :size="14" />仅保存在本机</span>
        </footer>
      </article>
    </main>

    <aside class="publishing-rail">
      <header><strong>发布设置</strong><Sparkles :size="16" /></header>
      <section>
        <label>栏目
          <select v-model="draft.category">
            <option value="article">篇章</option>
            <option value="note">笔记</option>
            <option value="milestone">轨迹</option>
          </select>
        </label>
        <label>路径
          <input v-model="draft.slug" type="text" />
        </label>
      </section>
      <section>
        <h2>公开边界</h2>
        <p><ShieldCheck :size="15" />发布前必须由后端完成证据与隐私检查。</p>
        <ul>
          <li><Check :size="14" />私有会话不公开</li>
          <li><Check :size="14" />内部路径不公开</li>
          <li><Check :size="14" />仅索引批准后的证据</li>
        </ul>
      </section>
      <section>
        <h2>写作入口（私有）</h2>
        <p><Sparkles :size="15" />这里是作者侧入口：草稿只保存在本机。公开站 `/notes` 只读展示已发布内容，不会放“写笔记”按钮。</p>
      </section>
      <section class="contract-status">
        <ListChecks :size="17" />
        <span><strong>等待发布契约</strong><small>审核通过后才能导出到公开 notes 包</small></span>
      </section>
    </aside>
  </div>
</template>

<style scoped>
.publishing-studio { display:grid; grid-template-columns:minmax(0,1fr) 286px; grid-template-rows:62px minmax(0,1fr); width:100%; height:100dvh; min-width:0; min-height:0; color:var(--sage-text); background:var(--sage-surface-muted); overflow:hidden; }
.studio-header { grid-column:1; display:flex; align-items:center; justify-content:space-between; gap:14px; padding:0 18px; border-bottom:1px solid var(--sage-border); background:var(--sage-surface); }.studio-header>div { display:flex; align-items:center; gap:9px; }.studio-header a { color:inherit; text-decoration:none; }.studio-header>div:first-child>a { display:grid; place-items:center; width:30px; height:30px; border-radius:var(--sage-radius); }.studio-header span { display:grid; }.studio-header small { color:var(--sage-text-muted); font-size:var(--sage-font-xs); }.studio-actions { flex-wrap:wrap; justify-content:flex-end; }.studio-actions button,.studio-actions a { display:inline-flex; align-items:center; justify-content:center; gap:6px; min-height:32px; padding:0 10px; border:1px solid var(--sage-border); border-radius:var(--sage-radius); color:var(--sage-text-secondary); background:var(--sage-surface); font-size:var(--sage-font-xs); }.studio-actions .external-preview { color:white; border-color:var(--sage-brand-strong); background:var(--sage-brand-strong); }.publish-button:disabled { color:var(--sage-text-muted); background:var(--sage-surface-muted); }
.studio-main { min-width:0; min-height:0; overflow:auto; padding:34px clamp(22px,4vw,54px); }.editor-sheet { display:flex; flex-direction:column; width:min(760px,100%); min-height:calc(100dvh - 130px); margin:0 auto; padding:54px clamp(24px,6vw,68px) 24px; background:var(--sage-surface); }.title-input,.summary-input,.body-input { width:100%; padding:0; border:0; outline:0; color:var(--sage-text); background:transparent; }.title-input { font-size:24px; font-weight:700; line-height:1.35; }.summary-input { min-height:70px; margin-top:14px; resize:none; color:var(--sage-text-muted); font-size:var(--sage-font-body); line-height:1.7; }.editor-toolbar { display:flex; align-items:center; gap:2px; min-height:48px; margin-top:18px; border-top:1px solid var(--sage-border); border-bottom:1px solid var(--sage-border); }.editor-toolbar button { display:grid; place-items:center; width:34px; height:34px; padding:0; border:0; border-radius:var(--sage-radius-sm); color:var(--sage-text-secondary); background:transparent; }.editor-toolbar button:hover { color:var(--sage-brand-strong); background:var(--sage-brand-bg); }.body-input { flex:1; min-height:500px; padding-top:28px; resize:none; font-family:var(--sage-font-sans); font-size:16px; line-height:1.9; }.preview-heading { padding-bottom:24px; border-bottom:1px solid var(--sage-border); }.preview-heading h1 { margin:0; font-size:30px; line-height:1.3; }.preview-heading p { margin:12px 0 0; color:var(--sage-text-muted); }.markdown-preview { padding:24px 0; font-size:16px; line-height:1.85; }.editor-footer { display:flex; justify-content:space-between; gap:16px; margin-top:auto; padding-top:16px; border-top:1px solid var(--sage-border); color:var(--sage-text-muted); font-size:var(--sage-font-xs); }.editor-footer span { display:flex; align-items:center; gap:5px; }
.publishing-rail { grid-column:2; grid-row:1/3; min-width:0; min-height:0; overflow:auto; border-left:1px solid var(--sage-border); background:var(--sage-surface); }.publishing-rail>header { display:flex; align-items:center; justify-content:space-between; min-height:62px; padding:0 16px; border-bottom:1px solid var(--sage-border); }.publishing-rail section { display:grid; gap:13px; padding:18px; border-bottom:1px solid var(--sage-border); }.publishing-rail label { display:grid; gap:6px; color:var(--sage-text-secondary); font-size:var(--sage-font-xs); }.publishing-rail input,.publishing-rail select { width:100%; height:36px; padding:0 9px; border:1px solid var(--sage-border); border-radius:var(--sage-radius); color:var(--sage-text); background:var(--sage-surface); }.publishing-rail h2 { margin:0; font-size:var(--sage-font-md); }.publishing-rail p { display:flex; align-items:flex-start; gap:7px; margin:0; color:var(--sage-text-muted); font-size:var(--sage-font-xs); line-height:1.55; }.publishing-rail ul { display:grid; gap:7px; margin:0; padding:0; list-style:none; }.publishing-rail li { display:flex; align-items:center; gap:7px; color:var(--sage-text-secondary); font-size:var(--sage-font-xs); }.contract-status { grid-template-columns:20px minmax(0,1fr); align-items:center; margin-top:auto; background:var(--sage-surface-muted); }.contract-status span { display:grid; }.contract-status small { overflow:hidden; color:var(--sage-text-muted); font-size:10px; text-overflow:ellipsis; white-space:nowrap; }
@media (max-width:900px) { .publishing-studio { display:block; height:auto; min-height:100dvh; overflow:visible; }.studio-header { position:sticky; z-index:5; top:0; min-height:58px; padding-left:56px; }.publishing-rail { display:none; }.studio-main { padding:0; }.editor-sheet { min-height:calc(100dvh - 58px); padding:32px 20px 20px; }.studio-actions .preview-button { display:none; } }
@media (max-width:520px) { .studio-header>div:first-child span small,.studio-actions .external-preview svg { display:none; }.studio-actions a { padding:0 8px; }.studio-actions .publish-button { display:none; }.title-input { font-size:21px; }.body-input { min-height:560px; font-size:15px; }.editor-sheet { padding-right:18px; padding-left:18px; } }
</style>
