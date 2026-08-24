<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import {
  AlertTriangle, ArrowRight, ArrowUp, BookOpenText, CheckCircle2, FlaskConical,
  GraduationCap, MessageCircle, Paperclip, RefreshCw, SearchCheck, ShieldCheck, Target,
} from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'
import { useAssistantHomeStore } from '../stores/assistantHome'
import { useCodingStore } from '../stores/coding'
import { runViewTransition } from '../composables/useViewTransition'
import type { LearningSourcePolicy, LearningTaskResponse } from '../types/api'

const home = useAssistantHomeStore()
const coding = useCodingStore()
const router = useRouter()
const route = useRoute()
const entryMode = ref<'learning' | 'chat'>('learning')
const prompt = ref('')
const promptInput = ref<HTMLTextAreaElement | null>(null)
const sending = ref(false)
const confirming = ref(false)
const sendError = ref('')
const formError = ref('')
const topic = ref('')
const desiredOutcome = ref('')
const startingLevel = ref<'' | 'beginner' | 'intermediate' | 'advanced'>('')
const timeBudget = ref<number | null>(null)
const targetDate = ref('')
const knowledgePolicy = ref<LearningSourcePolicy['knowledge']>('preferred')
const webPolicy = ref<LearningSourcePolicy['web']>('allowed_when_insufficient')
const freshness = ref<LearningSourcePolicy['freshness']>('all')
const domainsText = ref('')

const canSend = computed(() => Boolean(prompt.value.trim()) && !sending.value)
const recentSession = computed(() => home.summary?.sessions.items[0] ?? null)
const pendingCount = computed(() => {
  const proposals = home.summary?.proposals
  return proposals ? proposals.memory_pending + proposals.wiki_pending + proposals.note_pending : 0
})
const knowledgeReady = computed(() => (home.summary?.knowledge.source_count ?? 0) > 0)
const contextLine = computed(() => {
  if (recentSession.value) return `可以继续「${recentSession.value.title}」，也可以从一个新目标开始。`
  if (!knowledgeReady.value) return '告诉 Sage 你的目标，或先连接已有的知识库。'
  return '从已有知识出发，研究、练习，并把有价值的结果沉淀回来。'
})
const promptIdeas = computed(() => [
  recentSession.value ? '整理最近对话的结论与下一步' : '帮我设定一个今天可以完成的目标',
  knowledgeReady.value ? '从我的知识库里找一个值得深入研究的薄弱点' : '告诉我怎样导入 Obsidian 或 GitHub 知识库',
  '深度研究一个问题，并给出可引用的结论',
  '把当前目标变成一项可验证的练习',
])
const taskBusy = computed(() => confirming.value || home.learningState === 'activating' || home.learningState === 'loading')
const sourcePolicyLabel = computed(() => {
  const knowledge = knowledgePolicy.value === 'required'
    ? '必须使用本地知识'
    : knowledgePolicy.value === 'disabled' ? '不使用本地知识' : '优先使用本地知识'
  const web = webPolicy.value === 'forbidden' ? '不联网' : '证据不足时允许联网'
  return `${knowledge}，${web}${freshness.value === 'current' ? '，仅使用当前资料' : ''}`
})
const riskNotice = computed(() => home.learningTask?.risk_notice || '确认前不会启动运行时；激活后仅开放只读学习能力。')
const localReady = computed(() => Boolean(
  topic.value.trim() && desiredOutcome.value.trim() && startingLevel.value
  && timeBudget.value !== null && timeBudget.value >= 15,
))
const formDirty = computed(() => {
  const task = home.learningTask
  if (!task) return false
  return topic.value.trim() !== task.topic
    || desiredOutcome.value.trim() !== (task.desired_outcome ?? '')
    || (startingLevel.value || null) !== task.learner_profile.starting_level
    || timeBudget.value !== task.learner_profile.time_budget_minutes_per_week
    || (targetDate.value || null) !== task.learner_profile.target_date
    || knowledgePolicy.value !== task.source_policy.knowledge
    || webPolicy.value !== task.source_policy.web
    || freshness.value !== task.source_policy.freshness
    || normalizedDomains().join(',') !== task.source_policy.domains.join(',')
})
const confirmationLabel = computed(() => {
  if (home.learningState === 'activation_failed') return '重试激活'
  return localReady.value ? '确认并进入学习会话' : '保存澄清'
})
const taskStateLabel = computed(() => {
  if (home.learningState === 'activation_failed') return '激活失败'
  if (home.learningState === 'activating') return '正在激活'
  if (home.learningState === 'active') return '已激活'
  if (home.learningState === 'loading') return '正在保存'
  return home.learningTask?.clarification.ready_to_activate ? '等待确认' : '需要澄清'
})

watch(
  () => home.learningTask ? `${home.learningTask.task_id}:${home.learningTask.task_revision}` : '',
  () => syncTaskForm(home.learningTask),
  { immediate: true },
)

function normalizedDomains() {
  return domainsText.value.split(',').map((domain) => domain.trim().toLowerCase()).filter(Boolean)
}

function syncTaskForm(task: LearningTaskResponse | null) {
  if (!task) return
  topic.value = task.topic
  desiredOutcome.value = task.desired_outcome ?? ''
  startingLevel.value = task.learner_profile.starting_level ?? ''
  timeBudget.value = task.learner_profile.time_budget_minutes_per_week
  targetDate.value = task.learner_profile.target_date ?? ''
  knowledgePolicy.value = task.source_policy.knowledge
  webPolicy.value = task.source_policy.web
  freshness.value = task.source_policy.freshness
  domainsText.value = task.source_policy.domains.join(', ')
  formError.value = ''
}

async function send() {
  const content = prompt.value.trim()
  if (!content || sending.value) return
  sending.value = true
  sendError.value = ''
  try {
    if (entryMode.value === 'learning') {
      await home.createLearningTaskDraft({ topic: content })
      return
    }
    const sessionId = await coding.startSessionWithPrompt(content)
    if (!sessionId) throw new Error('新会话没有成功建立')
    localStorage.setItem('sage.coding.recentSessionId', sessionId)
    await routeToSession(sessionId)
  } catch (cause) {
    sendError.value = cause instanceof Error ? cause.message : '无法开始对话'
  } finally {
    sending.value = false
  }
}

async function confirmAndEnter() {
  if (taskBusy.value || !home.learningTask) return
  confirming.value = true
  formError.value = ''
  try {
    let task = home.learningTask
    if (formDirty.value) {
      task = await home.updateCurrentLearningTask({
        topic: topic.value.trim(), desired_outcome: desiredOutcome.value.trim() || null,
        starting_level: startingLevel.value || null, time_budget_minutes_per_week: timeBudget.value,
        target_date: targetDate.value || null,
        source_policy: {
          knowledge: knowledgePolicy.value, web: webPolicy.value,
          domains: normalizedDomains(), freshness: freshness.value,
        },
      })
    }
    if (!task.clarification.ready_to_activate) {
      formError.value = '请先完成必要澄清，再确认学习任务。'
      return
    }
    const receipt = await home.activateCurrentLearningTask()
    await coding.enterActivatedSessionWithPrompt(receipt.session_id, task.topic)
    localStorage.setItem('sage.coding.recentSessionId', receipt.session_id)
    await routeToSession(receipt.session_id)
  } catch (cause) {
    formError.value = cause instanceof Error ? cause.message : '学习任务激活失败'
  } finally {
    confirming.value = false
  }
}

async function continueActiveSession() {
  const sessionId = home.activationReceipt?.session_id
  if (!sessionId || confirming.value) return
  confirming.value = true
  formError.value = ''
  try {
    await coding.selectSession(sessionId)
    localStorage.setItem('sage.coding.recentSessionId', sessionId)
    await routeToSession(sessionId)
  } catch (cause) {
    formError.value = cause instanceof Error ? cause.message : '无法恢复学习会话'
  } finally {
    confirming.value = false
  }
}

function routeToSession(sessionId: string) {
  return runViewTransition(
    async () => { await router.push(`/coding/session/${encodeURIComponent(sessionId)}`) },
    'composer',
  )
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
  if (route.query.action !== 'compose' || home.learningState !== 'draft') return
  void nextTick(() => promptInput.value?.focus())
}

function startAnotherTask() {
  home.startAnotherLearningTask()
  prompt.value = ''
  void nextTick(() => promptInput.value?.focus())
}

watch(() => route.query.action, focusComposer)
onMounted(() => {
  void home.load()
  void home.restoreLearningTask()
  focusComposer()
})
</script>

<template>
  <div class="assistant-home">
    <main class="new-conversation" aria-labelledby="new-conversation-title">
      <div v-if="home.learningState === 'loading' && !home.learningTask" class="learning-loading" aria-live="polite">
        <RefreshCw :size="20" class="spin" /><strong>正在恢复学习任务</strong><span>以服务端状态为准</span>
      </div>

      <template v-else-if="home.learningState === 'draft'">
        <div class="home-intro">
          <span class="intro-mark"><Target :size="18" /></span>
          <h1 id="new-conversation-title">今天想推进什么？</h1>
          <p>{{ contextLine }}</p>
        </div>
        <div class="entry-mode" role="group" aria-label="开始方式">
          <button type="button" :aria-pressed="entryMode === 'learning'" aria-label="学习任务模式" @click="entryMode = 'learning'"><GraduationCap :size="15" />学习任务</button>
          <button type="button" :aria-pressed="entryMode === 'chat'" aria-label="直接对话模式" @click="entryMode = 'chat'"><MessageCircle :size="15" />直接对话</button>
        </div>
        <div class="prompt-ideas" aria-label="建议的开始方式">
          <button v-for="idea in promptIdeas" :key="idea" type="button" :disabled="sending" @click="usePrompt(idea)">{{ idea }}</button>
        </div>
        <section class="home-composer" aria-label="新对话输入框">
          <textarea ref="promptInput" v-model="prompt" rows="3" :disabled="sending" placeholder="描述一个目标、问题或想完成的任务" aria-label="给 Sage 的消息" @keydown="handleKeydown"></textarea>
          <div class="composer-footer">
            <div class="composer-tools">
              <RouterLink to="/knowledge?intent=import" aria-label="导入知识库" title="导入知识库"><Paperclip :size="17" /></RouterLink>
              <button type="button" title="深度研究" @click="usePrompt('深度研究一个问题，并给出可引用的结论')"><SearchCheck :size="17" /><span>研究</span></button>
              <button type="button" title="开始练习" @click="usePrompt('把当前目标变成一项可验证的练习')"><FlaskConical :size="17" /><span>练习</span></button>
            </div>
            <button class="home-send" type="button" :disabled="!canSend" :aria-label="sending ? '正在处理' : entryMode === 'learning' ? '创建学习任务' : '发送消息'" :title="entryMode === 'learning' ? '创建学习任务' : '发送消息'" @click="send"><RefreshCw v-if="sending" :size="17" class="spin" /><ArrowUp v-else :size="18" /></button>
          </div>
        </section>
        <p v-if="sendError || home.learningError" class="home-error" role="alert">{{ sendError || home.learningError }}</p>
        <div v-if="home.loading && !home.summary" class="home-state" aria-live="polite"><i></i><span>正在读取你的最近进度...</span></div>
        <div v-else-if="home.error && !home.summary" class="home-state error" role="alert"><span>{{ home.error }}</span><button type="button" @click="home.load(true)">重试</button></div>
        <div v-else-if="home.summary" class="home-context" aria-label="个人工作区概况">
          <RouterLink v-if="recentSession" :to="recentSession.target" class="continue-session"><span><small>继续最近对话</small><strong>{{ recentSession.title }}</strong></span><ArrowRight :size="16" /></RouterLink>
          <RouterLink v-else to="/knowledge?intent=import" class="continue-session"><span><small>建立你的知识上下文</small><strong>连接 Obsidian、Markdown 或 GitHub</strong></span><ArrowRight :size="16" /></RouterLink>
          <div class="workspace-signals"><span><BookOpenText :size="14" />{{ home.summary.knowledge.wiki_page_count }} 页知识</span><span v-if="pendingCount">{{ pendingCount }} 条待确认沉淀</span><span v-else>沉淀已清</span></div>
        </div>
      </template>

      <section v-else-if="home.learningTask" class="learning-confirmation" aria-labelledby="new-conversation-title">
        <header class="task-heading">
          <div><span class="task-kicker"><GraduationCap :size="15" />学习任务</span><h1 id="new-conversation-title">确认学习任务</h1><p>核对目标和来源边界后，再建立共享会话。</p></div>
          <span class="task-status" :class="home.learningState">{{ taskStateLabel }}</span>
        </header>
        <div class="task-form">
          <label class="wide-field"><span>学习主题</span><input v-model="topic" :disabled="taskBusy || home.learningState === 'active'" aria-label="学习主题" /></label>
          <label class="wide-field"><span>期望结果</span><textarea v-model="desiredOutcome" rows="3" :disabled="taskBusy || home.learningState === 'active'" aria-label="学习结果"></textarea></label>
          <label><span>当前基础</span><select v-model="startingLevel" :disabled="taskBusy || home.learningState === 'active'" aria-label="当前基础"><option value="">请选择</option><option value="beginner">初学</option><option value="intermediate">已有基础</option><option value="advanced">进阶</option></select></label>
          <label><span>每周投入（分钟）</span><input v-model.number="timeBudget" type="number" min="15" max="10080" :disabled="taskBusy || home.learningState === 'active'" aria-label="每周投入分钟" /></label>
          <label><span>目标日期</span><input v-model="targetDate" type="date" :disabled="taskBusy || home.learningState === 'active'" aria-label="目标日期" /></label>
        </div>
        <div class="policy-band" aria-label="来源策略">
          <div class="policy-title"><BookOpenText :size="17" /><span><small>来源策略</small><strong>{{ sourcePolicyLabel }}</strong></span></div>
          <div class="policy-controls">
            <label><span>本地知识</span><select v-model="knowledgePolicy" :disabled="taskBusy || home.learningState === 'active'" aria-label="本地知识策略"><option value="preferred">优先</option><option value="required">必须</option><option value="disabled">关闭</option></select></label>
            <label><span>联网</span><select v-model="webPolicy" :disabled="taskBusy || home.learningState === 'active'" aria-label="联网策略"><option value="allowed_when_insufficient">证据不足时允许</option><option value="forbidden">禁止</option></select></label>
            <label><span>时效</span><select v-model="freshness" :disabled="taskBusy || home.learningState === 'active'" aria-label="资料时效"><option value="all">不限</option><option value="current">当前</option></select></label>
            <label class="domain-field"><span>限定域名</span><input v-model="domainsText" :disabled="taskBusy || webPolicy === 'forbidden' || home.learningState === 'active'" placeholder="example.com, docs.example.com" aria-label="限定域名" /></label>
          </div>
        </div>
        <ul v-if="home.learningTask.clarification.questions.length" class="clarification-list" aria-label="仍需澄清"><li v-for="question in home.learningTask.clarification.questions" :key="question.field"><AlertTriangle :size="15" />{{ question.prompt }}</li></ul>
        <p class="risk-line" :class="home.learningTask.risk_class"><ShieldCheck :size="16" />{{ riskNotice }}</p>
        <p v-if="formError || home.learningError" class="home-error" role="alert">{{ formError || home.learningError }}</p>
        <div class="task-actions">
          <template v-if="home.learningState === 'active'">
            <button class="primary-action" type="button" :disabled="confirming" aria-label="继续学习会话" @click="continueActiveSession"><CheckCircle2 :size="17" />继续学习会话</button>
            <button class="secondary-action" type="button" :disabled="confirming" @click="startAnotherTask">新建学习任务</button>
          </template>
          <template v-else>
            <button class="secondary-action" type="button" :disabled="taskBusy" @click="startAnotherTask">稍后处理</button>
            <button class="primary-action" type="button" :disabled="taskBusy" :aria-label="confirmationLabel" @click="confirmAndEnter"><RefreshCw v-if="taskBusy" :size="17" class="spin" /><ArrowRight v-else :size="17" />{{ home.learningState === 'activating' ? '正在建立可恢复会话' : confirmationLabel }}</button>
          </template>
        </div>
      </section>
    </main>
  </div>
</template>

<style scoped>
.assistant-home{display:grid;width:100%;min-width:0;min-height:100dvh;overflow-x:hidden;padding:48px 28px;box-sizing:border-box;background:var(--sage-surface)}
.new-conversation{display:flex;width:min(860px,100%);max-width:100%;min-width:0;min-height:calc(100dvh - 96px);margin:auto;box-sizing:border-box;flex-direction:column;align-items:center;justify-content:center;padding-bottom:3vh}
.home-intro{display:grid;justify-items:center;max-width:620px;text-align:center}.intro-mark{display:grid;place-items:center;width:38px;height:38px;margin-bottom:16px;border:1px solid var(--sage-border);border-radius:50%;color:var(--sage-brand-strong);background:var(--sage-surface-raised);box-shadow:var(--sage-shadow-sm)}
.home-intro h1,.task-heading h1{margin:0;font-size:32px;line-height:1.25;letter-spacing:0}.home-intro p,.task-heading p{margin:10px 0 0;color:var(--sage-text-muted);font-size:var(--sage-font-sm);line-height:1.6}
.entry-mode{display:grid;grid-template-columns:1fr 1fr;gap:2px;margin-top:24px;padding:3px;border:1px solid var(--sage-border);border-radius:8px;background:var(--sage-surface-muted)}.entry-mode button{display:flex;align-items:center;justify-content:center;gap:6px;min-width:116px;height:32px;padding:0 10px;border:0;border-radius:5px;color:var(--sage-text-muted);background:transparent;font-size:var(--sage-font-xs)}.entry-mode button[aria-pressed=true]{color:var(--sage-text);background:var(--sage-surface-raised);box-shadow:var(--sage-shadow-sm)}
.prompt-ideas{display:flex;justify-content:center;flex-wrap:wrap;gap:8px;width:100%;max-width:760px;min-width:0;margin-top:18px}.prompt-ideas button{min-height:36px;max-width:100%;padding:0 13px;overflow:hidden;border:1px solid var(--sage-border);border-radius:18px;color:var(--sage-text-secondary);background:var(--sage-surface-muted);font-size:var(--sage-font-xs);text-overflow:ellipsis;white-space:nowrap}.prompt-ideas button:hover{border-color:var(--sage-border-strong);color:var(--sage-text);background:var(--sage-surface-raised)}
.home-composer{view-transition-name:sage-composer;width:min(760px,100%);max-width:100%;min-width:0;margin-top:18px;overflow:hidden;box-sizing:border-box;border:1px solid var(--sage-border-strong);border-radius:18px;background:var(--sage-surface-raised);box-shadow:0 12px 34px rgba(28,39,49,.09);transition:border-color .16s ease,box-shadow .16s ease}.home-composer:focus-within{border-color:color-mix(in srgb,var(--sage-brand-strong) 64%,var(--sage-border));box-shadow:0 14px 38px rgba(28,39,49,.12),0 0 0 2px color-mix(in srgb,var(--sage-brand) 12%,transparent)}.home-composer textarea{display:block;width:100%;min-height:92px;resize:none;padding:18px 20px 10px;border:0;outline:0;color:var(--sage-text);background:transparent;font-size:var(--sage-font-body);line-height:1.65}.home-composer textarea::placeholder{color:var(--sage-text-muted)}
.composer-footer{display:flex;align-items:center;justify-content:space-between;gap:14px;min-height:54px;padding:7px 9px 9px 13px}.composer-tools{display:flex;align-items:center;gap:3px;min-width:0}.composer-tools a,.composer-tools button{display:flex;align-items:center;justify-content:center;gap:5px;min-width:34px;height:34px;padding:0 8px;border:0;border-radius:8px;color:var(--sage-text-muted);background:transparent;text-decoration:none;font-size:var(--sage-font-xs)}.composer-tools a:hover,.composer-tools button:hover{color:var(--sage-text);background:var(--sage-surface-muted)}.home-send{display:grid;place-items:center;width:38px;height:38px;flex:none;padding:0;border:0;border-radius:50%;color:white;background:var(--sage-text)}.home-send:disabled{color:var(--sage-text-muted);background:var(--sage-surface-muted)}
.spin{animation:spin .9s linear infinite}.home-error{width:100%;margin:10px 0 0;color:var(--sage-danger);font-size:var(--sage-font-xs);line-height:1.5}.home-state{display:flex;align-items:center;gap:9px;min-height:48px;margin-top:16px;color:var(--sage-text-muted);font-size:var(--sage-font-xs)}.home-state i{width:7px;height:7px;border-radius:50%;background:var(--sage-source);animation:pulse 1.1s ease-in-out infinite}.home-state.error{color:var(--sage-danger)}.home-state button{min-height:30px;padding:0 9px;border:1px solid var(--sage-border);border-radius:var(--sage-radius-sm);color:var(--sage-text-secondary);background:transparent}
.home-context{display:grid;width:min(760px,100%);margin-top:22px;border-top:1px solid var(--sage-border)}.continue-session{display:flex;align-items:center;justify-content:space-between;gap:16px;min-height:62px;color:var(--sage-text);text-decoration:none}.continue-session:hover{color:var(--sage-source)}.continue-session span{display:grid;min-width:0;gap:3px}.continue-session small{color:var(--sage-text-muted);font-size:10px}.continue-session strong{overflow:hidden;font-size:var(--sage-font-sm);text-overflow:ellipsis;white-space:nowrap}.continue-session svg{flex:none;color:var(--sage-text-muted)}.workspace-signals{display:flex;align-items:center;gap:16px;padding-top:10px;border-top:1px solid var(--sage-border);color:var(--sage-text-muted);font-size:10px}.workspace-signals span{display:flex;align-items:center;gap:5px}
.learning-loading{display:grid;justify-items:center;gap:9px;color:var(--sage-text-muted)}.learning-loading strong{color:var(--sage-text);font-size:var(--sage-font-body)}.learning-loading span{font-size:var(--sage-font-xs)}.learning-confirmation{width:min(820px,100%);min-width:0;padding:4px 0}.task-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:24px;padding-bottom:24px;border-bottom:1px solid var(--sage-border)}.task-heading h1{margin-top:8px;font-size:27px}.task-kicker{display:flex;align-items:center;gap:6px;color:var(--sage-brand-strong);font-size:var(--sage-font-xs);font-weight:650}.task-status{flex:none;min-width:76px;padding:6px 9px;border:1px solid var(--sage-border);border-radius:6px;color:var(--sage-text-muted);background:var(--sage-surface-muted);font-size:11px;text-align:center}.task-status.active{border-color:color-mix(in srgb,var(--sage-success) 40%,var(--sage-border));color:var(--sage-success)}.task-status.activation_failed{border-color:color-mix(in srgb,var(--sage-danger) 45%,var(--sage-border));color:var(--sage-danger)}.task-status.activating,.task-status.loading{color:var(--sage-source)}
.task-form{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px 14px;padding:24px 0}.task-form label,.policy-controls label{display:grid;min-width:0;gap:7px;color:var(--sage-text-muted);font-size:11px}.task-form .wide-field{grid-column:1/-1}.task-form input,.task-form textarea,.task-form select,.policy-controls input,.policy-controls select{width:100%;min-width:0;box-sizing:border-box;border:1px solid var(--sage-border);border-radius:6px;outline:0;color:var(--sage-text);background:var(--sage-surface-raised);font:inherit;font-size:var(--sage-font-sm)}.task-form input,.task-form select,.policy-controls input,.policy-controls select{height:40px;padding:0 10px}.task-form textarea{min-height:76px;resize:vertical;padding:10px;line-height:1.55}.task-form input:focus,.task-form textarea:focus,.task-form select:focus,.policy-controls input:focus,.policy-controls select:focus{border-color:var(--sage-brand-strong);box-shadow:0 0 0 2px color-mix(in srgb,var(--sage-brand) 12%,transparent)}.task-form :disabled,.policy-controls :disabled{color:var(--sage-text-muted);background:var(--sage-surface-muted)}
.policy-band{display:grid;grid-template-columns:210px minmax(0,1fr);gap:20px;padding:20px 0;border-top:1px solid var(--sage-border);border-bottom:1px solid var(--sage-border)}.policy-title{display:flex;align-items:flex-start;gap:9px;color:var(--sage-source)}.policy-title span{display:grid;gap:5px;min-width:0}.policy-title small{color:var(--sage-text-muted);font-size:10px}.policy-title strong{color:var(--sage-text);font-size:var(--sage-font-xs);line-height:1.5}.policy-controls{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.policy-controls .domain-field{grid-column:1/-1}.clarification-list{display:grid;gap:7px;margin:18px 0 0;padding:0;list-style:none;color:var(--sage-warning);font-size:var(--sage-font-xs)}.clarification-list li{display:flex;align-items:flex-start;gap:7px}.clarification-list svg{flex:none;margin-top:1px}.risk-line{display:flex;align-items:flex-start;gap:8px;margin:18px 0 0;color:var(--sage-text-muted);font-size:var(--sage-font-xs);line-height:1.55}.risk-line svg{flex:none;color:var(--sage-success)}.risk-line.financial_education svg{color:var(--sage-warning)}
.task-actions{display:flex;align-items:center;justify-content:flex-end;gap:9px;padding-top:24px}.task-actions button{display:flex;align-items:center;justify-content:center;gap:7px;min-height:40px;padding:0 15px;border-radius:6px;font-size:var(--sage-font-xs)}.primary-action{border:1px solid var(--sage-text);color:var(--sage-surface);background:var(--sage-text)}.secondary-action{border:1px solid var(--sage-border);color:var(--sage-text-secondary);background:transparent}.task-actions button:disabled{opacity:.55}
@keyframes spin{to{transform:rotate(360deg)}}@keyframes pulse{50%{opacity:.35}}
@media (max-width:899px){.assistant-home{min-height:calc(100dvh - 64px);padding-top:64px}.new-conversation{min-height:calc(100dvh - 156px)}.policy-band{grid-template-columns:1fr}}
@media (max-width:600px){.assistant-home{padding-right:14px;padding-left:14px}.new-conversation{justify-content:flex-start;padding-top:10vh}.home-intro{max-width:100%}.home-intro h1{font-size:27px}.home-intro p{max-width:330px}.entry-mode{width:100%;box-sizing:border-box}.entry-mode button{min-width:0}.prompt-ideas{width:100%;max-width:100%;justify-content:stretch;margin-top:20px}.prompt-ideas button{width:100%;min-height:42px;padding:8px 13px;line-height:1.4;text-align:left;white-space:normal}.prompt-ideas button:nth-child(n+3){display:none}.home-composer{border-radius:15px}.composer-tools button span{display:none}.workspace-signals{gap:10px;flex-wrap:wrap}.continue-session strong{font-size:var(--sage-font-xs)}.learning-confirmation{padding-top:0}.task-heading{align-items:flex-start}.task-heading h1{font-size:23px}.task-status{min-width:68px}.task-form{grid-template-columns:1fr}.task-form .wide-field{grid-column:auto}.policy-controls{grid-template-columns:1fr}.policy-controls .domain-field{grid-column:auto}.task-actions{align-items:stretch;flex-direction:column-reverse}.task-actions button{width:100%}}
@media (prefers-reduced-motion:reduce){.spin,.home-state i{animation:none}.home-composer{transition:none}}
</style>
