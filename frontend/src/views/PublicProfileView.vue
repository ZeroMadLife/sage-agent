<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  ArrowRight,
  ArrowUp,
  CalendarDays,
  MessageCircle,
  Sprout,
  X,
} from 'lucide-vue-next'
import { useRoute } from 'vue-router'
import { useMarkdown } from '../composables/useMarkdown'
import { loadPublishingDraft } from '../harness/publishingDraft'

const route = useRoute()
const drawerOpen = ref(false)
const question = ref('')
const activeSection = ref<'home' | 'articles' | 'notes' | 'path' | 'about'>('home')
const draft = loadPublishingDraft()
const previewingDraft = computed(() => route.query.preview === 'draft')
const { render } = useMarkdown()
const draftBody = computed(() => render(draft.body))

const notes = [
  ['07.19', '重新划分 Sage 的产品边界：主对话负责行动，知识库负责观察，成长站负责公开表达。'],
  ['07.18', '恢复机制最难的部分不是重连，而是证明哪些副作用已经发生。'],
  ['07.16', '一个标签只有能支撑提问、练习和证据验证时，才值得成为知识节点。'],
] as const
</script>

<template>
  <div class="public-profile">
    <header class="public-header">
      <button class="public-brand" type="button" @click="activeSection = 'home'"><span>personal</span><strong>field notes</strong></button>
      <nav aria-label="公开站点导航">
        <button :class="{ active: activeSection === 'home' }" @click="activeSection = 'home'">起点</button>
        <button :class="{ active: activeSection === 'articles' }" @click="activeSection = 'articles'">篇章</button>
        <button :class="{ active: activeSection === 'notes' }" @click="activeSection = 'notes'">笔记</button>
        <button :class="{ active: activeSection === 'path' }" @click="activeSection = 'path'">轨迹</button>
        <button :class="{ active: activeSection === 'about' }" @click="activeSection = 'about'">关于</button>
      </nav>
      <button class="ask-sage" type="button" @click="drawerOpen = true"><MessageCircle :size="15" />问 Sage</button>
    </header>

    <main>
      <article v-if="previewingDraft" class="draft-preview">
        <p>Draft preview</p><h1>{{ draft.title }}</h1><strong>{{ draft.summary }}</strong>
        <div class="message-content" v-html="draftBody"></div>
      </article>

      <template v-else>
        <section class="public-intro">
          <span><Sprout :size="22" /></span>
          <h1>持续构建，也持续理解</h1>
          <p>这里记录我在 AI 系统、知识工程与产品实践中的文章、笔记和项目轨迹。</p>
          <div><button type="button" @click="activeSection = 'articles'">阅读篇章<ArrowRight :size="15" /></button><button type="button" @click="activeSection = 'notes'">最近笔记</button><button type="button" @click="activeSection = 'path'">成长轨迹</button></div>
        </section>

        <section class="latest-writing">
          <header><div><span>Latest writing</span><h2>最近篇章</h2></div><button type="button" @click="activeSection = 'articles'">全部篇章<ArrowRight :size="15" /></button></header>
          <article>
            <div class="article-visual" aria-label="Checkpoint 到 Timeline 的恢复流程"><span>Checkpoint</span><i></i><span>Timeline</span><i></i><span>Recover</span></div>
            <div><small>AI Systems · 2026/07/18</small><h3>如何让 Agent Harness 支持可靠恢复</h3><p>从状态快照、事件时间线到幂等执行，记录一次把“能跑”变成“可恢复、可验证”的工程实践。</p><RouterLink to="/public?preview=draft">阅读文章<ArrowRight :size="15" /></RouterLink></div>
          </article>
        </section>

        <section class="public-notes">
          <header><div><span>Field notes</span><h2>最近笔记</h2></div><button type="button" @click="activeSection = 'notes'">全部笔记<ArrowRight :size="15" /></button></header>
          <div><article v-for="note in notes" :key="note[0]"><time>{{ note[0] }}</time><p>{{ note[1] }}</p></article></div>
        </section>

        <section class="public-path">
          <header><div><span>Learning path</span><h2>成长轨迹</h2></div></header>
          <ol><li><i></i><time>2026 · 07</time><strong>可靠 Agent Harness</strong><p>完成恢复、审批、持久 Timeline 与知识沉淀的产品闭环。</p></li><li><i></i><time>2026 · 06</time><strong>个人知识库</strong><p>建立来源、Wiki、检索与图谱的分层模型。</p></li><li><i></i><time>2026 · 04</time><strong>RAG 实践</strong><p>从检索实验走向可评估、可引用的系统。</p></li></ol>
        </section>
      </template>
    </main>

    <footer><strong>Personal Field Notes</strong><p>记录真实学习，发布可验证作品。</p><div><a href="#">RSS</a><a href="#">GitHub</a><button type="button" @click="drawerOpen = true">Ask Sage</button></div></footer>

    <aside v-if="drawerOpen" class="public-agent" aria-label="公开 Sage 助手">
      <header><div><span><Sprout :size="17" /></span><p><strong>问 Sage</strong><small>只依据本站公开内容</small></p></div><button type="button" aria-label="关闭公开助手" title="关闭" @click="drawerOpen = false"><X :size="17" /></button></header>
      <section><p>可以问这里展示过的文章、项目与成长经历。</p><button type="button" @click="question = '这段经历验证了哪些能力？'">这段经历验证了哪些能力？</button><button type="button" @click="question = '有哪些可靠性工程实践？'">有哪些可靠性工程实践？</button><button type="button" @click="question = '推荐一篇关于知识库的文章'">推荐一篇关于知识库的文章</button></section>
      <div class="public-agent-status"><CalendarDays :size="15" /><span><strong>公开 Agent 尚未接入</strong><small>当前只提供问题草稿，不生成虚假回答。</small></span></div>
      <form @submit.prevent><textarea v-model="question" rows="3" placeholder="询问公开内容…"></textarea><button type="submit" disabled aria-label="等待公开 Agent API" title="等待公开 Agent API"><ArrowUp :size="16" /></button></form>
    </aside>
  </div>
</template>

<style scoped>
.public-profile { position:relative; min-height:100dvh; color:#1e2422; background:#fff; font-family:var(--sage-font-sans); overflow:hidden; }.public-header { display:grid; grid-template-columns:1fr auto 1fr; align-items:center; width:min(1180px,calc(100% - 40px)); min-height:72px; margin:0 auto; border-bottom:1px solid #e2e6e3; }.public-brand { display:flex; gap:4px; justify-self:start; padding:0; border:0; color:#327252; background:transparent; }.public-brand strong { color:#1e2422; }.public-header nav { display:flex; gap:22px; }.public-header nav button { min-height:36px; padding:0; border:0; border-bottom:1px solid transparent; color:#4e5852; background:transparent; font-size:13px; }.public-header nav button.active { color:#1e2422; border-bottom-color:#327252; }.ask-sage { display:flex; align-items:center; gap:6px; justify-self:end; min-height:34px; padding:0 11px; border:1px solid #d9dfdb; border-radius:6px; color:#1e2422; background:#fff; }
.public-profile main { width:min(980px,calc(100% - 40px)); margin:0 auto; }.public-intro { display:flex; flex-direction:column; align-items:center; min-height:430px; justify-content:center; padding:60px 0; border-bottom:1px solid #e2e6e3; text-align:center; }.public-intro>span { display:grid; place-items:center; width:56px; height:56px; border-radius:50%; color:#fff; background:#3d805d; }.public-intro h1 { margin:24px 0 10px; font-size:38px; line-height:1.25; }.public-intro p { max-width:620px; margin:0; color:#65736b; }.public-intro>div { display:flex; gap:22px; margin-top:25px; }.public-intro button,.latest-writing header button,.public-notes header button { display:flex; align-items:center; gap:5px; padding:0; border:0; color:#1e2422; background:transparent; }.public-intro button:first-child { color:#327252; }
.latest-writing,.public-notes,.public-path { padding:54px 0; border-bottom:1px solid #e2e6e3; }.latest-writing>header,.public-notes>header,.public-path>header { display:flex; align-items:flex-end; justify-content:space-between; gap:16px; margin-bottom:22px; }.latest-writing header span,.public-notes header span,.public-path header span,.draft-preview>p { color:#758078; font-size:11px; }.latest-writing h2,.public-notes h2,.public-path h2 { margin:3px 0 0; font-size:20px; }.latest-writing article { display:grid; grid-template-columns:minmax(0,1fr) minmax(300px,.9fr); align-items:center; gap:32px; }.article-visual { display:flex; flex-direction:column; align-items:center; justify-content:center; min-height:280px; color:#eff7f2; background:#202623; }.article-visual span { padding:8px 12px; border:1px solid #607168; border-radius:5px; }.article-visual i { width:1px; height:28px; background:#4d8b63; }.latest-writing article small { color:#327252; }.latest-writing h3 { margin:12px 0 8px; font-size:22px; }.latest-writing article p { color:#65736b; line-height:1.75; }.latest-writing article a { display:inline-flex; align-items:center; gap:5px; color:#327252; text-decoration:none; }
.public-notes>div { border-top:1px solid #e2e6e3; }.public-notes article { display:grid; grid-template-columns:58px minmax(0,1fr); gap:10px; padding:18px 0; border-bottom:1px solid #e2e6e3; }.public-notes time { color:#758078; font-size:12px; }.public-notes p { margin:0; }.public-path ol { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); margin:0; padding:0; list-style:none; }.public-path li { position:relative; padding:22px 28px 0 0; border-top:1px solid #dbe1dd; }.public-path i { position:absolute; top:-5px; left:0; width:9px; height:9px; border-radius:50%; background:#3d805d; }.public-path time,.public-path strong { display:block; }.public-path time { color:#758078; font-size:12px; }.public-path strong { margin-top:8px; }.public-path p { margin:5px 0 0; color:#65736b; font-size:13px; line-height:1.55; }.public-profile>footer { display:grid; grid-template-columns:auto 1fr auto; align-items:center; gap:24px; width:min(1180px,calc(100% - 40px)); min-height:110px; margin:0 auto; }.public-profile>footer p { color:#758078; }.public-profile>footer div { display:flex; gap:18px; }.public-profile>footer a,.public-profile>footer button { padding:0; border:0; color:#4e5852; background:transparent; text-decoration:none; }
.public-agent { position:fixed; z-index:20; inset:0 0 0 auto; display:flex; flex-direction:column; width:min(380px,100%); border-left:1px solid #d9dfdb; background:#fff; box-shadow:-16px 0 40px rgb(17 24 39 / 12%); }.public-agent>header { display:flex; align-items:center; justify-content:space-between; min-height:68px; padding:0 16px; border-bottom:1px solid #e2e6e3; }.public-agent header>div { display:flex; align-items:center; gap:9px; }.public-agent header>div>span { display:grid; place-items:center; width:32px; height:32px; border-radius:6px; color:#fff; background:#3d805d; }.public-agent header p,.public-agent header strong,.public-agent header small { display:block; margin:0; }.public-agent header small { color:#758078; font-size:11px; }.public-agent header>button { display:grid; place-items:center; width:30px; height:30px; border:0; background:transparent; }.public-agent>section { display:grid; padding:18px; }.public-agent>section p { color:#65736b; }.public-agent>section button { min-height:46px; border:0; border-top:1px solid #e2e6e3; color:#1e2422; background:transparent; text-align:left; }.public-agent-status { display:flex; align-items:center; gap:8px; margin:0 18px; padding:12px 0; border-top:1px solid #e2e6e3; color:#65736b; }.public-agent-status span { display:grid; }.public-agent-status strong { font-size:13px; }.public-agent-status small { font-size:11px; }.public-agent form { display:grid; grid-template-columns:minmax(0,1fr) 34px; align-items:end; gap:8px; margin:auto 12px 12px; padding:10px; border:1px solid #d9dfdb; border-radius:6px; }.public-agent textarea { resize:none; border:0; outline:0; }.public-agent form button { display:grid; place-items:center; width:34px; height:34px; border:0; border-radius:6px; color:#8a938d; background:#edf0ee; }
.draft-preview { width:min(760px,100%); margin:0 auto; padding:72px 0 100px; }.draft-preview h1 { margin:7px 0 14px; font-size:38px; line-height:1.25; }.draft-preview>strong { display:block; color:#65736b; font-weight:400; line-height:1.7; }.draft-preview .message-content { margin-top:36px; padding-top:24px; border-top:1px solid #e2e6e3; font-size:16px; line-height:1.9; }
@media (max-width:700px) { .public-header { grid-template-columns:1fr auto; width:calc(100% - 32px); }.public-header nav { display:none; }.public-profile main { width:calc(100% - 36px); }.public-intro { min-height:390px; }.public-intro h1 { font-size:29px; }.latest-writing article { grid-template-columns:1fr; }.article-visual { min-height:220px; }.public-path ol { grid-template-columns:1fr; gap:30px; }.public-profile>footer { grid-template-columns:1fr auto; width:calc(100% - 32px); padding:24px 0; }.public-profile>footer p { display:none; }.draft-preview { padding-top:48px; }.draft-preview h1 { font-size:28px; } }
</style>
