<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import {
  ArrowRight,
  ArrowUp,
  BookOpenText,
  FlaskConical,
  Paperclip,
  RefreshCw,
  SearchCheck,
  Target,
} from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'
import { useAssistantHomeStore } from '../stores/assistantHome'
import { useCodingStore } from '../stores/coding'
import { runViewTransition } from '../composables/useViewTransition'

const home = useAssistantHomeStore()
const coding = useCodingStore()
const router = useRouter()
const route = useRoute()
const prompt = ref('')
const promptInput = ref<HTMLTextAreaElement | null>(null)
const sending = ref(false)
const sendError = ref('')
const canSend = computed(() => Boolean(prompt.value.trim()) && !sending.value)
const recentSession = computed(() => home.summary?.sessions.items[0] ?? null)
const pendingCount = computed(() => {
  const proposals = home.summary?.proposals
  return proposals
    ? proposals.memory_pending + proposals.wiki_pending + proposals.note_pending
    : 0
})
const knowledgeReady = computed(() => (home.summary?.knowledge.source_count ?? 0) > 0)
const contextLine = computed(() => {
  if (recentSession.value) return `可以继续「${recentSession.value.title}」，也可以从一个新目标开始。`
  if (!knowledgeReady.value) return '告诉 Sage 你的目标，或先连接已有的知识库。'
  return '从已有知识出发，研究、练习，并把有价值的结果沉淀回来。'
})
const promptIdeas = computed(() => [
  recentSession.value
    ? '整理最近对话的结论与下一步'
    : '帮我设定一个今天可以完成的目标',
  knowledgeReady.value
    ? '从我的知识库里找一个值得深入研究的薄弱点'
    : '告诉我怎样导入 Obsidian 或 GitHub 知识库',
  '深度研究一个问题，并给出可引用的结论',
  '把当前目标变成一项可验证的练习',
])

async function send() {
  const content = prompt.value.trim()
  if (!content || sending.value) return
  sending.value = true
  sendError.value = ''
  try {
    const sessionId = await coding.startSessionWithPrompt(content)
    if (!sessionId) throw new Error('新会话没有成功建立')
    localStorage.setItem('sage.coding.recentSessionId', sessionId)
    await runViewTransition(
      async () => {
        await router.push(`/coding/session/${encodeURIComponent(sessionId)}`)
      },
      'composer',
    )
  } catch (cause) {
    sendError.value = cause instanceof Error ? cause.message : '无法开始对话'
  } finally {
    sending.value = false
  }
}

function handleKeydown(event: KeyboardEvent) {
  if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
    event.preventDefault()
    void send()
  }
}

function usePrompt(value: string) {
  prompt.value = value
  void nextTick(() => promptInput.value?.focus())
}

function focusComposer() {
  if (route.query.action !== 'compose') return
  void nextTick(() => promptInput.value?.focus())
}

watch(() => route.query.action, focusComposer)
onMounted(() => {
  void home.load()
  focusComposer()
})
</script>

<template>
  <div class="assistant-home">
    <main class="new-conversation" aria-labelledby="new-conversation-title">
      <div class="home-intro">
        <span class="intro-mark"><Target :size="18" /></span>
        <h1 id="new-conversation-title">今天想推进什么？</h1>
        <p>{{ contextLine }}</p>
      </div>

      <div class="prompt-ideas" aria-label="建议的开始方式">
        <button v-for="idea in promptIdeas" :key="idea" type="button" :disabled="sending" @click="usePrompt(idea)">{{ idea }}</button>
      </div>

      <section class="home-composer" aria-label="新对话输入框">
        <textarea
          ref="promptInput"
          v-model="prompt"
          rows="3"
          :disabled="sending"
          placeholder="描述一个目标、问题或想完成的任务"
          aria-label="给 Sage 的消息"
          @keydown="handleKeydown"
        ></textarea>
        <div class="composer-footer">
          <div class="composer-tools">
            <RouterLink to="/knowledge?intent=import" aria-label="导入知识库" title="导入知识库"><Paperclip :size="17" /></RouterLink>
            <button type="button" title="深度研究" @click="usePrompt('深度研究一个问题，并给出可引用的结论')"><SearchCheck :size="17" /><span>研究</span></button>
            <button type="button" title="开始练习" @click="usePrompt('把当前目标变成一项可验证的练习')"><FlaskConical :size="17" /><span>练习</span></button>
          </div>
          <button class="home-send" type="button" :disabled="!canSend" :aria-label="sending ? '正在建立会话' : '发送消息'" :title="sending ? '正在建立会话' : '发送消息'" @click="send"><RefreshCw v-if="sending" :size="17" class="spin" /><ArrowUp v-else :size="18" /></button>
        </div>
      </section>
      <p v-if="sendError" class="home-error" role="alert">{{ sendError }}</p>

      <div v-if="home.loading && !home.summary" class="home-state" aria-live="polite"><i></i><span>正在读取你的最近进度...</span></div>
      <div v-else-if="home.error && !home.summary" class="home-state error" role="alert"><span>{{ home.error }}</span><button type="button" @click="home.load(true)">重试</button></div>
      <div v-else-if="home.summary" class="home-context" aria-label="个人工作区概况">
        <RouterLink v-if="recentSession" :to="recentSession.target" class="continue-session">
          <span><small>继续最近对话</small><strong>{{ recentSession.title }}</strong></span><ArrowRight :size="16" />
        </RouterLink>
        <RouterLink v-else to="/knowledge?intent=import" class="continue-session">
          <span><small>建立你的知识上下文</small><strong>连接 Obsidian、Markdown 或 GitHub</strong></span><ArrowRight :size="16" />
        </RouterLink>
        <div class="workspace-signals">
          <span><BookOpenText :size="14" />{{ home.summary.knowledge.wiki_page_count }} 页知识</span>
          <span v-if="pendingCount">{{ pendingCount }} 条待确认沉淀</span>
          <span v-else>沉淀已清</span>
        </div>
      </div>
    </main>
  </div>
</template>

<style scoped>
.assistant-home { display:grid; width:100%; min-width:0; min-height:100dvh; overflow-x:hidden; padding:48px 28px; box-sizing:border-box; background:var(--sage-surface); }
.new-conversation { display:flex; width:min(820px,100%); max-width:100%; min-width:0; min-height:calc(100dvh - 96px); margin:auto; box-sizing:border-box; flex-direction:column; align-items:center; justify-content:center; padding-bottom:3vh; }
.home-intro { display:grid; justify-items:center; max-width:620px; text-align:center; }
.intro-mark { display:grid; place-items:center; width:38px; height:38px; margin-bottom:16px; border:1px solid var(--sage-border); border-radius:50%; color:var(--sage-brand-strong); background:var(--sage-surface-raised); box-shadow:var(--sage-shadow-sm); }
.home-intro h1 { margin:0; font-size:32px; line-height:1.25; letter-spacing:0; }
.home-intro p { margin:10px 0 0; color:var(--sage-text-muted); font-size:var(--sage-font-sm); line-height:1.6; }
.prompt-ideas { display:flex; justify-content:center; flex-wrap:wrap; gap:8px; width:100%; max-width:760px; min-width:0; margin-top:28px; }
.prompt-ideas button { min-height:36px; max-width:100%; padding:0 13px; overflow:hidden; border:1px solid var(--sage-border); border-radius:18px; color:var(--sage-text-secondary); background:var(--sage-surface-muted); font-size:var(--sage-font-xs); text-overflow:ellipsis; white-space:nowrap; }
.prompt-ideas button:hover { border-color:var(--sage-border-strong); color:var(--sage-text); background:var(--sage-surface-raised); }
.home-composer { view-transition-name:sage-composer; width:min(760px,100%); max-width:100%; min-width:0; margin-top:22px; overflow:hidden; box-sizing:border-box; border:1px solid var(--sage-border-strong); border-radius:18px; background:var(--sage-surface-raised); box-shadow:0 12px 34px rgba(28,39,49,.09); transition:border-color .16s ease,box-shadow .16s ease; }
.home-composer:focus-within { border-color:color-mix(in srgb,var(--sage-brand-strong) 64%,var(--sage-border)); box-shadow:0 14px 38px rgba(28,39,49,.12),0 0 0 2px color-mix(in srgb,var(--sage-brand) 12%,transparent); }
.home-composer textarea { display:block; width:100%; min-height:92px; resize:none; padding:18px 20px 10px; border:0; outline:0; color:var(--sage-text); background:transparent; font-size:var(--sage-font-body); line-height:1.65; }
.home-composer textarea::placeholder { color:var(--sage-text-muted); }
.composer-footer { display:flex; align-items:center; justify-content:space-between; gap:14px; min-height:54px; padding:7px 9px 9px 13px; }
.composer-tools { display:flex; align-items:center; gap:3px; min-width:0; }
.composer-tools a,.composer-tools button { display:flex; align-items:center; justify-content:center; gap:5px; min-width:34px; height:34px; padding:0 8px; border:0; border-radius:8px; color:var(--sage-text-muted); background:transparent; text-decoration:none; font-size:var(--sage-font-xs); }
.composer-tools a:hover,.composer-tools button:hover { color:var(--sage-text); background:var(--sage-surface-muted); }
.home-send { display:grid; place-items:center; width:38px; height:38px; flex:none; padding:0; border:0; border-radius:50%; color:white; background:var(--sage-text); }
.home-send:disabled { color:var(--sage-text-muted); background:var(--sage-surface-muted); }
.spin { animation:spin .9s linear infinite; }
.home-error { width:min(760px,100%); margin:8px 0 0; color:var(--sage-danger); font-size:var(--sage-font-xs); }
.home-state { display:flex; align-items:center; gap:9px; min-height:48px; margin-top:16px; color:var(--sage-text-muted); font-size:var(--sage-font-xs); }.home-state i { width:7px; height:7px; border-radius:50%; background:var(--sage-source); animation:pulse 1.1s ease-in-out infinite; }.home-state.error { color:var(--sage-danger); }.home-state button { min-height:30px; padding:0 9px; border:1px solid var(--sage-border); border-radius:var(--sage-radius-sm); color:var(--sage-text-secondary); background:transparent; }
.home-context { display:grid; width:min(760px,100%); margin-top:22px; border-top:1px solid var(--sage-border); }
.continue-session { display:flex; align-items:center; justify-content:space-between; gap:16px; min-height:62px; color:var(--sage-text); text-decoration:none; }.continue-session:hover { color:var(--sage-source); }.continue-session span { display:grid; min-width:0; gap:3px; }.continue-session small { color:var(--sage-text-muted); font-size:10px; }.continue-session strong { overflow:hidden; font-size:var(--sage-font-sm); text-overflow:ellipsis; white-space:nowrap; }.continue-session svg { flex:none; color:var(--sage-text-muted); }
.workspace-signals { display:flex; align-items:center; gap:16px; padding-top:10px; border-top:1px solid var(--sage-border); color:var(--sage-text-muted); font-size:10px; }.workspace-signals span { display:flex; align-items:center; gap:5px; }
@keyframes spin { to { transform:rotate(360deg); } }@keyframes pulse { 50% { opacity:.35; } }
@media (max-width:899px) { .assistant-home { min-height:calc(100dvh - 64px); padding-top:64px; }.new-conversation { min-height:calc(100dvh - 156px); } }
@media (max-width:600px) { .assistant-home { padding-right:14px; padding-left:14px; }.new-conversation { justify-content:flex-start; padding-top:12vh; }.home-intro { max-width:100%; }.home-intro h1 { font-size:27px; }.home-intro p { max-width:330px; }.prompt-ideas { width:100%; max-width:100%; justify-content:stretch; margin-top:24px; }.prompt-ideas button { width:100%; min-height:42px; padding:8px 13px; line-height:1.4; text-align:left; white-space:normal; }.prompt-ideas button:nth-child(n+3) { display:none; }.home-composer { border-radius:15px; }.composer-tools button span { display:none; }.workspace-signals { gap:10px; flex-wrap:wrap; }.continue-session strong { font-size:var(--sage-font-xs); } }
@media (prefers-reduced-motion:reduce) { .spin,.home-state i { animation:none; }.home-composer { transition:none; } }
</style>
