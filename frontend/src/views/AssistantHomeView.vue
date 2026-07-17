<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { ArrowUp, RefreshCw, Sparkles } from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'
import { AssistantHomeSummary } from '../components/assistant'
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
const today = new Intl.DateTimeFormat('zh-CN', {
  month: 'long', day: 'numeric', weekday: 'long',
}).format(new Date())
const canSend = computed(() => Boolean(prompt.value.trim()) && !sending.value)

const promptIdeas = [
  '继续复盘 Sage 的当前开发进度',
  '帮我把最近的项目经验整理成知识',
  '带我学习一个还没看懂的源码模块',
]

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
      <header class="home-header">
        <div><span>{{ today }}</span><h1>今天</h1><p>继续一个项目，理解一个问题，或把对话沉淀为可复用的知识。</p></div>
        <div v-if="home.summary" class="identity"><i></i><span><strong>{{ home.summary.identity.display_name }}</strong><small>{{ home.summary.identity.mode === 'cloud' ? '云端空间' : '本地模式' }}</small></span></div>
      </header>

      <section class="home-composer" aria-labelledby="composer-title">
        <div class="composer-label"><Sparkles :size="15" /><strong id="composer-title">和 Sage 开始</strong><span>⌘ Enter 发送</span></div>
        <textarea ref="promptInput" v-model="prompt" rows="4" :disabled="sending" placeholder="想了解什么、继续什么，或者希望沉淀什么？" aria-label="给 Sage 的消息" @keydown="handleKeydown"></textarea>
        <div class="composer-footer">
          <div class="prompt-ideas"><button v-for="idea in promptIdeas" :key="idea" type="button" :disabled="sending" @click="prompt = idea">{{ idea }}</button></div>
          <button class="home-send" type="button" :disabled="!canSend" :aria-label="sending ? '正在建立会话' : '发送消息'" :title="sending ? '正在建立会话' : '发送消息'" @click="send"><RefreshCw v-if="sending" :size="16" class="spin" /><ArrowUp v-else :size="17" /></button>
        </div>
      </section>
      <p v-if="sendError" class="home-error" role="alert">{{ sendError }}</p>

      <div v-if="home.loading && !home.summary" class="home-loading" aria-live="polite"><i></i><span>正在读取你的项目与对话...</span></div>
      <div v-else-if="home.error && !home.summary" class="home-load-error" role="alert"><span>{{ home.error }}</span><button type="button" @click="home.load(true)">重试</button></div>
      <template v-else-if="home.summary">
        <p v-if="home.error" class="refresh-error" role="status">{{ home.error }}</p>
        <AssistantHomeSummary :summary="home.summary" />
      </template>
  </div>
</template>

<style scoped>
.assistant-home { width:min(940px,100%); margin:0 auto; padding:54px 34px 50px; }.home-header { display:flex; align-items:flex-end; justify-content:space-between; gap:28px; }.home-header > div:first-child { min-width:0; }.home-header span:first-child { color:var(--sage-text-muted); font-size:var(--sage-font-xs); }.home-header h1 { margin:5px 0 5px; font-size:var(--sage-font-title); letter-spacing:0; }.home-header p { margin:0; color:var(--sage-text-secondary); font-size:var(--sage-font-md); }.identity { display:flex; align-items:center; gap:9px; flex:none; }.identity i { width:9px; height:9px; border-radius:50%; background:var(--sage-success); box-shadow:0 0 0 4px var(--sage-success-bg); }.identity span { display:flex; flex-direction:column; }.identity strong { font-size:var(--sage-font-sm); }.identity small { color:var(--sage-text-muted); font-size:var(--sage-font-xs); }
.home-composer { view-transition-name:sage-composer; margin-top:26px; border:1px solid var(--sage-border-strong); border-radius:var(--sage-radius-lg); background:var(--sage-surface); box-shadow:var(--sage-shadow-sm); overflow:hidden; }.home-composer:focus-within { border-color:var(--sage-brand-strong); box-shadow:0 0 0 2px color-mix(in srgb,var(--sage-brand) 16%,transparent); }.composer-label { display:flex; align-items:center; gap:7px; min-height:40px; padding:0 13px; border-bottom:1px solid var(--sage-border); color:var(--sage-brand-strong); font-size:var(--sage-font-sm); }.composer-label span { margin-left:auto; color:var(--sage-text-muted); font-size:var(--sage-font-xs); }.home-composer textarea { display:block; width:100%; min-height:112px; resize:vertical; padding:15px; border:0; outline:0; color:var(--sage-text); background:transparent; font-size:var(--sage-font-body); line-height:1.7; }.home-composer textarea::placeholder { color:var(--sage-text-muted); }.composer-footer { display:flex; align-items:flex-end; gap:12px; min-height:50px; padding:8px 9px 9px 12px; border-top:1px solid var(--sage-border); }.prompt-ideas { display:flex; gap:6px; flex:1; min-width:0; overflow-x:auto; scrollbar-width:none; }.prompt-ideas button { flex:none; min-height:30px; padding:0 10px; border:1px solid var(--sage-border); border-radius:var(--sage-radius); color:var(--sage-text-muted); background:var(--sage-surface); font-size:var(--sage-font-xs); }.prompt-ideas button:hover { color:var(--sage-source); border-color:var(--sage-source); background:var(--sage-source-bg); }.home-send { display:grid; place-items:center; width:34px; height:34px; flex:none; padding:0; border:0; border-radius:var(--sage-radius); color:white; background:var(--sage-brand-strong); }.home-send:disabled { color:var(--sage-text-muted); background:var(--sage-surface-muted); }.spin { animation:spin .9s linear infinite; }
.home-error,.refresh-error { margin:8px 0 0; color:var(--sage-danger); font-size:var(--sage-font-xs); }.home-loading,.home-load-error { display:flex; align-items:center; gap:10px; min-height:100px; margin-top:24px; border-top:1px solid var(--sage-border); border-bottom:1px solid var(--sage-border); color:var(--sage-text-muted); font-size:var(--sage-font-sm); }.home-loading i { width:8px; height:8px; border-radius:50%; background:var(--sage-source); animation:pulse 1.1s ease-in-out infinite; }.home-load-error { justify-content:space-between; color:var(--sage-danger); }.home-load-error button { min-height:32px; padding:0 11px; border:1px solid var(--sage-border); border-radius:var(--sage-radius); color:var(--sage-text-secondary); background:var(--sage-surface); font-size:var(--sage-font-sm); }
@keyframes spin { to { transform:rotate(360deg); } }@keyframes pulse { 50% { opacity:.35; } }
@media (max-width:899px) { .assistant-home { padding-top:70px; } }@media (max-width:600px) { .assistant-home { padding-right:16px; padding-left:16px; }.home-header { align-items:flex-start; }.identity { display:none; }.home-header h1 { font-size:26px; }.composer-label span { display:none; }.prompt-ideas { max-width:calc(100vw - 92px); } }@media (prefers-reduced-motion:reduce) { .spin,.home-loading i { animation:none; } }
</style>
