<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import {
  ArrowRight,
  ArrowUpRight,
  BookOpenText,
  Check,
  ChevronDown,
  CircleDot,
  ExternalLink,
  FileCheck2,
  Github,
  LockKeyhole,
  MessageCircle,
  RotateCcw,
  Send,
  ShieldCheck,
  Sparkles,
  Target,
  X,
} from 'lucide-vue-next'
import knowledgeWorkbenchImage from '../assets/public/sage-knowledge-workbench.jpg'
import {
  answerPublicProfileQuestion,
  type PublicAgentSource,
} from '../harness/publicAgent'

type AgentMessage = {
  role: 'visitor' | 'sage'
  text: string
  sources?: PublicAgentSource[]
}

const drawerOpen = ref(false)
const question = ref('')
const activeSection = ref('home')
const activeWork = ref<string | null>(null)
const isAnswering = ref(false)

const workItems = [
  {
    id: 'sage',
    index: '01',
    signal: '目标驱动',
    title: 'Goal Contract',
    description: 'Thread Goal 以 revision 与 CAS 持久化，每次 run 冻结自己的目标快照。',
    trace: 'revision + CAS',
    accent: '#2f704e',
    tags: ['Thread Goal', 'Run snapshot', 'Manual / Auto continue'],
    reason: '普通聊天只记住上一轮说了什么，却无法稳定说明任务目标、完成条件和下一次续跑是否仍指向同一版本。',
    decision: '把 Goal 作为 session 级契约写入既有事件日志；创建、修改、评估和继续都校验 revision，run 启动时冻结快照。',
    evidence: '当前分支已覆盖 Goal CRUD、旧 revision 拒绝、手动继续、自动 follow-up 上限和重启后幂等恢复测试。',
    boundary: 'Goal 默认关闭；没有明确目标时保留普通对话行为，也不会用模型自评直接宣布目标完成。',
    question: 'Sage 的目标驱动具体体现在哪里？',
    linkLabel: '查看 Thread Goal 实现',
    href: 'https://github.com/ZeroMadLife/sage-agent/blob/dev/sage-v7/core/harness/thread_goal.py',
  },
  {
    id: 'harness',
    index: '02',
    signal: '可恢复运行',
    title: 'Harness 2.0',
    description: 'Timeline、checkpoint、approval 和重放共用一条事实链，不靠界面猜测状态。',
    trace: 'timeline + checkpoint',
    accent: '#355f7a',
    tags: ['Durable Timeline', 'Checkpoint', 'Approval'],
    reason: '断线或刷新后重新发起一次请求，会重复工具副作用，也无法证明上一轮究竟执行到了哪里。',
    decision: '以 durable timeline 和 checkpoint 作为恢复依据；approval 继续已冻结的 run，上层页面只投影可审计事件。',
    evidence: 'Harness package、SessionEventJournal 和恢复测试共同覆盖有序事件、terminal-once、审批后续跑与重启恢复。',
    boundary: '恢复表示继续既有事实链，不表示后台一直在线，也不在公开页伪造 running、thinking 或工具执行状态。',
    question: 'Harness 2.0 如何保证刷新后还能恢复？',
    linkLabel: '查看 Session Event Journal',
    href: 'https://github.com/ZeroMadLife/sage-agent/blob/dev/sage-v7/core/coding/persistence/session_event_journal.py',
  },
  {
    id: 'mastery',
    index: '03',
    signal: '掌握证据',
    title: 'Mastery Evidence',
    description: '掌握度来自受控证据与固定 rubric，不来自模型口头判断或聊天次数。',
    trace: 'sage-mastery-v1',
    accent: '#a85e50',
    tags: ['Evidence Ledger', 'Deterministic rubric', 'Idempotent replay'],
    reason: '知识页面、聊天回答和模型自信度都不能证明一个人已经掌握某项能力。',
    decision: '只把经过验证的 evidence 写入跨会话 Ledger，并按 workspace、goal revision 与 capability 隔离；失效证据触发重算。',
    evidence: 'H2.7C 已交付幂等写入、冲突回滚、固定 rubric 投影、Knowledge 只读 API 和 Harness outbox 恢复链。',
    boundary: '当前只开放受控 code_test 候选；用户可见的 rubric 校正 UI 属于下一切片，不把单一证据包装成“已掌握”。',
    question: 'Mastery Evidence 为什么不是模型自评？',
    linkLabel: '查看 Mastery Ledger',
    href: 'https://github.com/ZeroMadLife/sage-agent/blob/dev/sage-v7/core/learning/mastery.py',
  },
]

const milestones = [
  {
    date: '2026 · 07',
    title: 'Goal 与 Mastery Evidence',
    detail: '目标版本、续跑边界与跨会话证据账本进入同一条可恢复运行路径。',
    status: '当前阶段',
  },
  {
    date: '2026 · 07',
    title: 'Chat Harness 2.0',
    detail: '统一 timeline、checkpoint、approval、tool、skill 与 subagent 事件。',
    status: '已验证',
  },
  {
    date: '2026 · 06',
    title: 'Personal Knowledge Base',
    detail: '来源、Wiki、混合检索、citation、revision 与知识图谱形成可追溯结构。',
    status: '已验证',
  },
  {
    date: '2026 · 04',
    title: 'Retrieval Practice',
    detail: '从 RAG 实验转向能被测试、引用并进入真实工程判断的检索实践。',
    status: '起点',
  },
]

const engineeringNotes = [
  {
    date: '07.20',
    category: 'Public Surface',
    title: '公开门面先证明边界，再证明能力',
    text: '公开 Ask Sage 只回答内置资料；私人 Knowledge、Memory、Session 和网络能力不进入静态构建。',
  },
  {
    date: '07.19',
    category: 'Recovery',
    title: '恢复不是重发一次请求',
    text: '真正的恢复必须知道哪些副作用已经发生，并从同一条 run 与 timeline 继续。',
  },
  {
    date: '07.18',
    category: 'Knowledge',
    title: 'Knowledge 负责观察，Chat 负责行动',
    text: '图谱提供结构和来源上下文；行动仍回到共享 Harness，避免制造第二套对话事实。',
  },
  {
    date: '07.16',
    category: 'Mastery',
    title: '证据先于掌握度',
    text: '单次通过、知识覆盖或模型判断都不等于掌握；Ledger 只保存可追溯、可失效的证据。',
  },
]

const agentMessages = ref<AgentMessage[]>([
  {
    role: 'sage',
    text: '这是受限公开资料问答，也是公开资料预览。我只回答这页已经公开的项目、方法和成长记录。',
  },
])

function selectSection(section: string) {
  activeSection.value = section
  const reduceMotion = typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  if (section === 'home') {
    window.scrollTo?.({ top: 0, behavior: reduceMotion ? 'auto' : 'smooth' })
    return
  }
  document.getElementById(section)?.scrollIntoView?.({
    behavior: reduceMotion ? 'auto' : 'smooth',
    block: 'start',
  })
}

function openAgent(prompt = '') {
  drawerOpen.value = true
  if (prompt) question.value = prompt
}

async function answerQuestion() {
  const value = question.value.trim()
  if (!value || isAnswering.value) return
  question.value = ''
  agentMessages.value.push({ role: 'visitor', text: value })
  isAnswering.value = true
  try {
    const response = await answerPublicProfileQuestion(value)
    agentMessages.value.push({ role: 'sage', text: response.answer, sources: response.sources })
  } finally {
    isAnswering.value = false
  }
}

function inspectWork(id: string) {
  activeWork.value = id
  selectSection('work')
}

function toggleWork(id: string) {
  activeWork.value = activeWork.value === id ? null : id
}

function openSource(source: PublicAgentSource) {
  drawerOpen.value = false
  selectSection(source.target)
}

let sectionObserver: IntersectionObserver | null = null

onMounted(() => {
  if (typeof IntersectionObserver === 'undefined') return
  sectionObserver = new IntersectionObserver(
    (entries) => {
      const visible = entries
        .filter((entry) => entry.isIntersecting)
        .sort((left, right) => right.intersectionRatio - left.intersectionRatio)[0]
      if (visible?.target.id) activeSection.value = visible.target.id
    },
    { rootMargin: '-18% 0px -64% 0px', threshold: [0.05, 0.2, 0.5] },
  )
  for (const section of document.querySelectorAll<HTMLElement>('#work, #path, #writing')) {
    sectionObserver.observe(section)
  }
})

onBeforeUnmount(() => sectionObserver?.disconnect())
</script>

<template>
  <div class="public-profile">
    <header class="public-header">
      <div class="public-header__inner">
        <button class="public-brand" type="button" @click="selectSection('home')">
          <span class="brand-mark"><Sparkles :size="15" /></span>
          <span><strong>ZeroMadLife</strong><small>/ Sage</small></span>
        </button>

        <nav aria-label="公开站点导航">
          <button type="button" data-section="work" :class="{ active: activeSection === 'work' }" @click="selectSection('work')">项目</button>
          <button type="button" data-section="path" :class="{ active: activeSection === 'path' }" @click="selectSection('path')">成长轨迹</button>
          <button type="button" data-section="writing" :class="{ active: activeSection === 'writing' }" @click="selectSection('writing')">工程笔记</button>
        </nav>

        <button class="ask-sage" type="button" @click="openAgent()">
          <MessageCircle :size="15" />Ask Sage
        </button>
      </div>
    </header>

    <main>
      <section id="top" class="public-hero">
        <div class="hero-inner">
          <div class="hero-copy">
            <h1>ZeroMadLife <em>/ Sage</em></h1>
            <p class="hero-positioning">Personal AI Learning Companion</p>
            <p class="hero-support">把目标、知识、真实实践和可验证成长连接成一个可恢复的个人学习系统。</p>

            <div class="hero-actions">
              <button class="primary-action" type="button" @click="selectSection('work')">
                查看工程证据 <ArrowRight :size="15" />
              </button>
              <a href="https://github.com/ZeroMadLife/sage-agent" target="_blank" rel="noreferrer">
                GitHub <ArrowUpRight :size="14" />
              </a>
            </div>

            <div class="hero-evidence" aria-label="三项核心工程证据">
              <button
                v-for="item in workItems"
                :key="item.id"
                type="button"
                class="hero-evidence__row"
                :style="{ '--evidence-accent': item.accent }"
                :data-hero-evidence="item.id"
                @click="inspectWork(item.id)"
              >
                <span>{{ item.index }}</span>
                <span>
                  <strong>{{ item.signal }} <em>{{ item.title }}</em></strong>
                  <small>{{ item.description }}</small>
                </span>
                <ArrowRight :size="14" />
              </button>
            </div>
          </div>

          <figure class="hero-product">
            <img :src="knowledgeWorkbenchImage" alt="Sage Knowledge 真实工作台截面" fetchpriority="high" />
            <figcaption>
              <span><strong>Sage Knowledge</strong><small>当前公开产品截面</small></span>
              <span>source snapshot · no private data</span>
            </figcaption>
          </figure>
        </div>
      </section>

      <section id="work" class="public-section project-section">
        <div class="section-inner">
          <header class="section-heading">
            <span>01 / PROJECT</span>
            <h2>Sage 不是聊天壳，<br />而是一套长期学习工程。</h2>
            <p>面向程序员与技术学习者：由目标进入主对话，用 Knowledge 组织上下文，在 Practice 中验证，再由 Evidence 约束长期沉淀。</p>
          </header>

          <div class="project-layout">
            <div class="project-copy">
              <span class="project-name"><Target :size="16" />Sage / Personal AI Learning Companion</span>
              <h3>目标驱动，证据收口。</h3>
              <p>主对话负责行动，Knowledge 负责观察，Coding 是按需进入的 Practice Engine。所有长期变化都经过 revision、证据或用户确认。</p>
              <dl class="project-facts">
                <div><dt>Goal</dt><dd>Thread Goal + frozen run snapshot</dd></div>
                <div><dt>Runtime</dt><dd>Harness 2.0 + durable timeline</dd></div>
                <div><dt>Learning</dt><dd>Mastery Ledger + fixed rubric</dd></div>
                <div><dt>Boundary</dt><dd>Local-first / controlled public surface</dd></div>
              </dl>
              <a class="inline-link" href="https://github.com/ZeroMadLife/sage-agent" target="_blank" rel="noreferrer">
                阅读源码与设计 <ExternalLink :size="14" />
              </a>
            </div>

            <figure class="product-preview">
              <div class="preview-bar">
                <span><i></i><i></i><i></i></span>
                <strong>Sage Knowledge / graph view</strong>
                <small>真实界面 · 公开演示数据</small>
              </div>
              <img :src="knowledgeWorkbenchImage" alt="Sage Knowledge 图谱工作台的真实界面" loading="lazy" />
              <figcaption><span>Knowledge graph</span><span>goal-aware · revision bound</span></figcaption>
            </figure>
          </div>

          <div class="evidence-heading">
            <span>ENGINEERING EVIDENCE</span>
            <strong>三条能追到代码与测试的判断</strong>
          </div>
          <div class="work-list">
            <div v-for="item in workItems" :key="item.id" class="work-entry">
              <button
                class="work-row"
                type="button"
                :style="{ '--evidence-accent': item.accent }"
                :data-work-id="item.id"
                :aria-expanded="activeWork === item.id"
                :aria-controls="`work-evidence-${item.id}`"
                @click="toggleWork(item.id)"
              >
                <span class="work-index">{{ item.index }}</span>
                <span class="work-main">
                  <span>{{ item.signal }}</span>
                  <strong>{{ item.title }}</strong>
                  <small>{{ item.description }}</small>
                </span>
                <span class="work-trace">{{ item.trace }}</span>
                <ChevronDown :size="17" class="row-arrow" :class="{ expanded: activeWork === item.id }" />
              </button>
              <section
                v-if="activeWork === item.id"
                :id="`work-evidence-${item.id}`"
                class="work-evidence"
                :data-work-evidence="item.id"
              >
                <div class="work-tags"><span v-for="tag in item.tags" :key="tag">{{ tag }}</span></div>
                <dl>
                  <div><dt>为什么做</dt><dd>{{ item.reason }}</dd></div>
                  <div><dt>核心取舍</dt><dd>{{ item.decision }}</dd></div>
                  <div><dt>怎么判断有效</dt><dd>{{ item.evidence }}</dd></div>
                  <div><dt>当前边界</dt><dd>{{ item.boundary }}</dd></div>
                </dl>
                <footer>
                  <a :href="item.href" target="_blank" rel="noreferrer">{{ item.linkLabel }} <ExternalLink :size="13" /></a>
                  <button type="button" @click="openAgent(item.question)"><MessageCircle :size="13" />围绕这项提问</button>
                </footer>
              </section>
            </div>
          </div>
        </div>
      </section>

      <section id="path" class="public-section path-section">
        <div class="section-inner">
          <header class="section-heading compact">
            <span>02 / LEARNING PATH</span>
            <h2>成长轨迹</h2>
            <p>只记录已经形成的工程切片，不把路线图当成交付。</p>
          </header>
          <ol class="milestone-list">
            <li v-for="(milestone, index) in milestones" :key="milestone.date + milestone.title">
              <span class="milestone-marker"><Check v-if="index > 0" :size="14" /><CircleDot v-else :size="14" /></span>
              <div>
                <time>{{ milestone.date }}</time>
                <strong>{{ milestone.title }}</strong>
                <p>{{ milestone.detail }}</p>
                <small>{{ milestone.status }}</small>
              </div>
            </li>
          </ol>
        </div>
      </section>

      <section id="writing" class="public-section notes-section">
        <div class="section-inner">
          <header class="section-heading compact">
            <span>03 / ENGINEERING NOTES</span>
            <h2>工程笔记</h2>
            <p>短记录保留关键判断；实现、测试与边界仍以仓库为准。</p>
          </header>
          <div class="notes-list">
            <article v-for="note in engineeringNotes" :key="note.date + note.title">
              <time>{{ note.date }}</time>
              <span>{{ note.category }}</span>
              <div><h3>{{ note.title }}</h3><p>{{ note.text }}</p></div>
              <BookOpenText :size="16" />
            </article>
          </div>
        </div>
      </section>

      <section class="ask-boundary" aria-labelledby="ask-boundary-title">
        <div class="section-inner ask-boundary__inner">
          <div>
            <span>ASK SAGE</span>
            <h2 id="ask-boundary-title">受限公开资料问答</h2>
            <p>用于快速理解公开项目，不连接私人工作台，也不冒充正在运行的公网 Agent。</p>
          </div>
          <ul>
            <li><FileCheck2 :size="15" />只回答已发布 corpus</li>
            <li><LockKeyhole :size="15" />不读取私人 Knowledge / Memory</li>
            <li><ShieldCheck :size="15" />静态构建，无外部 API 请求</li>
          </ul>
          <button type="button" @click="openAgent('Sage 是做什么的？')">
            打开 Ask Sage <ArrowRight :size="15" />
          </button>
        </div>
      </section>
    </main>

    <footer class="public-footer">
      <div class="section-inner public-footer__inner">
        <div><strong>ZeroMadLife / Sage</strong><span>Personal AI Learning Companion</span></div>
        <div class="footer-links">
          <a href="https://github.com/ZeroMadLife/sage-agent" target="_blank" rel="noreferrer"><Github :size="14" />GitHub</a>
          <button type="button" @click="openAgent()"><MessageCircle :size="14" />Ask Sage</button>
        </div>
      </div>
    </footer>

    <div v-if="drawerOpen" class="agent-layer">
      <button class="agent-scrim" type="button" aria-label="关闭公开问答" @click="drawerOpen = false"></button>
      <aside class="public-agent" role="dialog" aria-modal="true" aria-label="受限公开资料问答" @keydown.esc="drawerOpen = false">
        <header>
          <div class="agent-title">
            <span class="agent-mark"><Sparkles :size="16" /></span>
            <span><strong>Ask Sage</strong><small>受限公开资料问答</small></span>
          </div>
          <button class="icon-button" type="button" aria-label="关闭公开助手" title="关闭" @click="drawerOpen = false"><X :size="17" /></button>
        </header>

        <div class="agent-boundary">
          <span><FileCheck2 :size="13" />已发布资料</span>
          <span><LockKeyhole :size="13" />无私人数据</span>
          <span><RotateCcw :size="13" />确定性回答</span>
        </div>

        <section class="agent-body" aria-live="polite">
          <div v-for="(message, index) in agentMessages" :key="index" class="agent-message" :class="message.role">
            <span>{{ message.role === 'sage' ? 'Sage' : '你' }}</span>
            <p>{{ message.text }}</p>
            <div v-if="message.sources?.length" class="agent-sources">
              <strong>回答依据</strong>
              <button
                v-for="source in message.sources"
                :key="source.id"
                type="button"
                class="agent-source"
                :data-target="source.target"
                @click="openSource(source)"
              >
                <span>{{ source.label }}</span>
                <small>{{ source.detail }}</small>
                <ArrowRight :size="13" />
              </button>
            </div>
          </div>
        </section>

        <div class="agent-prompts">
          <span>公开问题</span>
          <button type="button" @click="openAgent('Sage 是做什么的？')">Sage 是做什么的？</button>
          <button type="button" @click="openAgent('Harness 2.0 解决什么问题？')">Harness 如何恢复运行？</button>
          <button type="button" @click="openAgent('Mastery Evidence 为什么不是模型自评？')">Mastery Evidence 如何成立？</button>
        </div>

        <form class="agent-form" :aria-busy="isAnswering" @submit.prevent="answerQuestion">
          <textarea v-model="question" rows="3" aria-label="询问公开资料" placeholder="询问公开的项目与方法…" :disabled="isAnswering"></textarea>
          <button type="submit" aria-label="发送问题" title="发送问题" :disabled="isAnswering || !question.trim()"><Send :size="16" /></button>
        </form>
        <p class="agent-disclaimer">静态公开 corpus · CSP connect-src none</p>
      </aside>
    </div>
  </div>
</template>

<style scoped>
:global(html) { scroll-behavior: smooth; }
:global(body) { background: #fff; }

.public-profile {
  --public-ink: #182019;
  --public-muted: #647168;
  --public-faint: #8a968d;
  --public-line: #dce4de;
  --public-line-strong: #c8d4cb;
  --public-green: #2f704e;
  --public-green-dark: #255b3f;
  --public-green-soft: #edf4ef;
  --public-band: #f4f7f5;
  --public-glint: #a9d4b5;
  min-height: 100dvh;
  overflow-x: clip;
  color: var(--public-ink);
  background: #fff;
  font-family: var(--sage-font-sans);
}

.public-profile button,
.public-profile textarea { font: inherit; }

.section-inner,
.public-header__inner,
.hero-inner {
  width: min(1328px, calc(100% - 96px));
  margin: 0 auto;
}

.public-header {
  position: sticky;
  z-index: 20;
  top: 0;
  border-bottom: 1px solid rgb(220 228 222 / 88%);
  background: rgb(255 255 255 / 96%);
  backdrop-filter: blur(12px);
}

.public-header::after {
  position: absolute;
  right: 0;
  bottom: -1px;
  width: 20%;
  height: 1px;
  background: linear-gradient(90deg, transparent, rgb(75 139 99 / 72%), transparent);
  content: '';
  pointer-events: none;
  animation: public-signal-line 8s ease-in-out infinite;
}

.public-header__inner {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  min-height: 68px;
}

.public-brand {
  display: inline-flex;
  align-items: center;
  justify-self: start;
  gap: 10px;
  padding: 0;
  border: 0;
  color: var(--public-ink);
  background: transparent;
  cursor: pointer;
}

.public-brand > span:last-child { display: flex; align-items: baseline; gap: 4px; }
.public-brand strong { font-size: 14px; letter-spacing: 0; }
.public-brand small { color: var(--public-green); font-size: 12px; }

.brand-mark,
.agent-mark {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  color: #fff;
  background: var(--public-green);
}

.brand-mark { border-radius: 50%; }

.brand-mark,
.ask-sage,
.primary-action,
.ask-boundary button {
  position: relative;
  overflow: hidden;
  isolation: isolate;
}

.brand-mark::after,
.ask-sage::after,
.primary-action::after,
.ask-boundary button::after {
  position: absolute;
  z-index: 0;
  inset: -40% auto -40% -45%;
  width: 30%;
  background: linear-gradient(90deg, transparent, rgb(255 255 255 / 34%), transparent);
  content: '';
  pointer-events: none;
  transform: skewX(-18deg) translateX(-260%);
}

.brand-mark::after { animation: public-mark-glint 7.5s ease-in-out infinite; }
.brand-mark > svg { position: relative; z-index: 1; }

.public-header nav { display: flex; align-items: center; gap: 32px; }
.public-header nav button {
  min-height: 40px;
  padding: 4px 0 0;
  border: 0;
  border-bottom: 1px solid transparent;
  color: var(--public-muted);
  background: transparent;
  font-size: 12px;
}
.public-header nav button:hover,
.public-header nav button.active { color: var(--public-ink); border-bottom-color: var(--public-green); }

.ask-sage,
.primary-action,
.ask-boundary button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  min-height: 38px;
  padding: 0 13px;
  border: 1px solid var(--public-green);
  border-radius: 4px;
  color: #fff;
  background: var(--public-green);
  font-size: 12px;
  font-weight: 650;
}
.ask-sage { justify-self: end; }
.ask-sage:hover,
.primary-action:hover,
.ask-boundary button:hover { border-color: var(--public-green-dark); background: var(--public-green-dark); }
.ask-sage:hover::after,
.ask-sage:focus-visible::after,
.primary-action:hover::after,
.primary-action:focus-visible::after,
.ask-boundary button:hover::after,
.ask-boundary button:focus-visible::after { animation: public-action-glint .72s ease-out; }

.public-hero { border-bottom: 1px solid var(--public-line); }
.hero-inner {
  display: grid;
  grid-template-columns: minmax(0, .92fr) minmax(540px, 1.08fr);
  gap: 76px;
  align-items: center;
  min-height: 714px;
  padding: 48px 0 56px;
}
.hero-copy { min-width: 0; animation: public-copy-reveal .72s cubic-bezier(.2, .72, .2, 1) both; }
.hero-copy h1 {
  margin: 0;
  font-size: 64px;
  line-height: 1.02;
  letter-spacing: 0;
}
.hero-copy h1 em {
  color: #6f9877;
  background: linear-gradient(100deg, #54745d 8%, #75a17f 40%, #cce8d3 50%, #75a17f 60%, #54745d 92%);
  background-size: 260% 100%;
  background-clip: text;
  font-style: normal;
  font-weight: 650;
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  animation: public-title-glint 9s ease-in-out infinite;
}
.hero-positioning { margin: 12px 0 0; font-size: 28px; line-height: 1.25; letter-spacing: 0; }
.hero-support { max-width: 570px; margin: 22px 0 0; color: var(--public-muted); font-size: 15px; line-height: 1.8; }
.hero-actions { display: flex; align-items: center; gap: 21px; margin-top: 26px; }
.hero-actions a,
.inline-link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--public-green);
  font-size: 12px;
  font-weight: 650;
  text-decoration: none;
}
.hero-actions a:hover,
.inline-link:hover { color: var(--public-ink); }

.hero-evidence { margin-top: 34px; border-top: 1px solid var(--public-line); animation: public-copy-reveal .72s .18s cubic-bezier(.2, .72, .2, 1) both; }
.hero-evidence__row {
  display: grid;
  grid-template-columns: 42px minmax(0, 1fr) auto;
  align-items: center;
  gap: 13px;
  width: 100%;
  min-height: 72px;
  padding: 10px 0;
  border: 0;
  border-bottom: 1px solid var(--public-line);
  color: var(--public-ink);
  background: transparent;
  text-align: left;
}
.hero-evidence__row > span:first-child { color: var(--evidence-accent); font-family: var(--sage-font-mono); font-size: 19px; }
.hero-evidence__row > span:nth-child(2) { display: grid; gap: 4px; }
.hero-evidence__row strong { font-size: 14px; font-weight: 700; }
.hero-evidence__row strong em { margin-left: 7px; color: var(--evidence-accent); font-family: var(--sage-font-mono); font-size: 11px; font-style: normal; font-weight: 500; }
.hero-evidence__row small { color: var(--public-muted); font-size: 11px; line-height: 1.55; }
.hero-evidence__row > svg { color: var(--public-faint); transition: transform .18s ease, color .18s ease; }
.hero-evidence__row:hover > svg { color: var(--evidence-accent); transform: translateX(3px); }

.hero-product {
  position: relative;
  align-self: stretch;
  display: flex;
  flex-direction: column;
  justify-content: center;
  min-width: 0;
  margin: 0;
  animation: public-product-reveal .82s .08s cubic-bezier(.2, .72, .2, 1) both;
}
.hero-product::before,
.product-preview::before {
  position: absolute;
  z-index: 2;
  top: 0;
  left: 0;
  width: 22%;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--public-glint), transparent);
  content: '';
  pointer-events: none;
  animation: public-frame-glint 7.2s ease-in-out infinite;
}
.hero-product img {
  display: block;
  width: 100%;
  aspect-ratio: 16 / 9;
  border: 1px solid var(--public-line-strong);
  border-radius: 4px 4px 0 0;
  object-fit: cover;
  box-shadow: 0 18px 42px rgb(38 68 48 / 9%);
}
.hero-product figcaption {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  min-height: 52px;
  padding: 9px 12px;
  border: 1px solid var(--public-line-strong);
  border-top: 0;
  border-radius: 0 0 4px 4px;
  color: var(--public-faint);
  background: #fff;
  font-family: var(--sage-font-mono);
  font-size: 9px;
}
.hero-product figcaption > span:first-child { display: grid; gap: 1px; }
.hero-product figcaption strong { color: #344039; font-family: var(--sage-font-sans); font-size: 11px; }
.hero-product figcaption small { color: var(--public-faint); font-family: var(--sage-font-sans); font-size: 9px; }

.public-section { scroll-margin-top: 68px; padding: 104px 0; border-bottom: 1px solid var(--public-line); }
.section-heading {
  display: grid;
  grid-template-columns: 150px minmax(0, 1fr) 290px;
  align-items: end;
  gap: 30px;
  margin-bottom: 54px;
}
.section-heading > span,
.evidence-heading > span,
.ask-boundary__inner > div > span {
  color: var(--public-faint);
  font-family: var(--sage-font-mono);
  font-size: 10px;
}
.section-heading h2 { margin: 0; font-size: 43px; line-height: 1.16; letter-spacing: 0; }
.section-heading > p { margin: 0; color: var(--public-muted); font-size: 12px; line-height: 1.75; }
.section-heading.compact h2 { font-size: 36px; }

.project-layout {
  display: grid;
  grid-template-columns: minmax(320px, .72fr) minmax(0, 1.28fr);
  gap: 72px;
  align-items: center;
}
.project-copy { max-width: 470px; }
.project-name { display: inline-flex; align-items: center; gap: 7px; color: var(--public-green); font-size: 12px; font-weight: 650; }
.project-copy h3 { margin: 18px 0 14px; font-size: 30px; line-height: 1.25; letter-spacing: 0; }
.project-copy > p { margin: 0; color: var(--public-muted); font-size: 14px; line-height: 1.85; }
.project-facts { margin: 30px 0 24px; border-top: 1px solid var(--public-line); }
.project-facts div { display: grid; grid-template-columns: 84px minmax(0, 1fr); gap: 14px; padding: 11px 0; border-bottom: 1px solid var(--public-line); }
.project-facts dt { color: var(--public-faint); font-size: 10px; }
.project-facts dd { margin: 0; color: #3f4b43; font-family: var(--sage-font-mono); font-size: 10px; }

.product-preview { position: relative; min-width: 0; margin: 0; overflow: hidden; padding: 9px 9px 0; border: 1px solid var(--public-line-strong); border-radius: 4px; background: #fff; box-shadow: 0 18px 42px rgb(38 68 48 / 8%); }
.preview-bar { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: 12px; min-height: 32px; color: var(--public-faint); font-size: 9px; }
.preview-bar > span { display: flex; gap: 4px; }
.preview-bar i { width: 6px; height: 6px; border-radius: 50%; background: #b7c6bb; }
.preview-bar i:first-child { background: #b86b5c; }
.preview-bar i:nth-child(2) { background: #c49b46; }
.preview-bar i:nth-child(3) { background: #659374; }
.preview-bar strong { color: #465249; font-size: 10px; }
.product-preview img { display: block; width: 100%; aspect-ratio: 16 / 9; border: 1px solid #e4e9e5; object-fit: cover; }
.product-preview figcaption { display: flex; justify-content: space-between; gap: 12px; padding: 9px 1px 8px; color: var(--public-faint); font-family: var(--sage-font-mono); font-size: 9px; }

.evidence-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 24px; margin-top: 86px; padding-bottom: 15px; border-bottom: 1px solid var(--public-line-strong); }
.evidence-heading strong { font-size: 13px; }
.work-list { border-bottom: 1px solid var(--public-line-strong); }
.work-entry { border-bottom: 1px solid var(--public-line); }
.work-entry:last-child { border-bottom: 0; }
.work-row {
  display: grid;
  grid-template-columns: 54px 150px minmax(0, 1fr) 160px 24px;
  align-items: center;
  gap: 16px;
  width: 100%;
  min-height: 104px;
  padding: 16px 0;
  border: 0;
  color: var(--public-ink);
  background: transparent;
  text-align: left;
}
.work-index { color: var(--evidence-accent); font-family: var(--sage-font-mono); font-size: 17px; }
.work-main { display: contents; }
.work-main > span { color: var(--public-muted); font-size: 11px; }
.work-main > strong { font-size: 17px; }
.work-main > small { color: var(--public-muted); font-size: 11px; line-height: 1.6; }
.work-trace { color: var(--evidence-accent); font-family: var(--sage-font-mono); font-size: 10px; text-align: right; }
.row-arrow { color: var(--public-faint); transition: transform .18s ease, color .18s ease; }
.row-arrow.expanded { color: var(--evidence-accent); transform: rotate(180deg); }
.work-row:hover .row-arrow { color: var(--evidence-accent); }
.work-evidence { padding: 2px 0 30px 70px; }
.work-tags { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 14px; }
.work-tags span { padding: 4px 7px; border: 1px solid var(--public-line); border-radius: 3px; color: var(--public-muted); background: var(--public-band); font-family: var(--sage-font-mono); font-size: 9px; }
.work-evidence dl { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); margin: 0; border-top: 1px solid var(--public-line); }
.work-evidence dl > div { padding: 17px 28px 17px 0; border-bottom: 1px solid var(--public-line); }
.work-evidence dl > div:nth-child(even) { padding-right: 0; padding-left: 28px; border-left: 1px solid var(--public-line); }
.work-evidence dt { color: var(--public-faint); font-size: 10px; font-weight: 650; }
.work-evidence dd { margin: 7px 0 0; color: #465249; font-size: 12px; line-height: 1.75; }
.work-evidence footer { display: flex; align-items: center; justify-content: space-between; gap: 18px; padding-top: 15px; }
.work-evidence footer a,
.work-evidence footer button { display: inline-flex; align-items: center; gap: 6px; padding: 0; border: 0; color: var(--public-green); background: transparent; font-size: 11px; font-weight: 650; text-decoration: none; }

.path-section { background: var(--public-band); }
.milestone-list { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); margin: 0; padding: 0; list-style: none; }
.milestone-list li { position: relative; display: grid; grid-template-columns: 34px minmax(0, 1fr); gap: 12px; min-width: 0; padding-right: 28px; }
.milestone-list li::after { position: absolute; z-index: 0; top: 17px; left: 34px; right: 0; height: 1px; background: #aebfb2; content: ''; }
.milestone-list li:last-child::after { display: none; }
.milestone-marker { position: relative; z-index: 1; display: grid; place-items: center; width: 34px; height: 34px; border: 1px solid #9eb1a2; border-radius: 50%; color: var(--public-green); background: var(--public-band); }
.milestone-list div { padding-top: 2px; }
.milestone-list time,
.milestone-list strong,
.milestone-list p,
.milestone-list small { display: block; }
.milestone-list time { color: var(--public-faint); font-family: var(--sage-font-mono); font-size: 9px; }
.milestone-list strong { margin-top: 8px; font-size: 14px; }
.milestone-list p { min-height: 58px; margin: 7px 0 0; color: var(--public-muted); font-size: 11px; line-height: 1.65; }
.milestone-list small { margin-top: 9px; color: var(--public-green); font-size: 10px; }

.notes-list { border-top: 1px solid var(--public-line-strong); border-bottom: 1px solid var(--public-line-strong); }
.notes-list article { display: grid; grid-template-columns: 64px 130px minmax(0, 1fr) auto; align-items: start; gap: 22px; padding: 23px 0; border-bottom: 1px solid var(--public-line); }
.notes-list article:last-child { border-bottom: 0; }
.notes-list time { color: var(--public-faint); font-family: var(--sage-font-mono); font-size: 10px; }
.notes-list article > span { color: var(--public-green); font-family: var(--sage-font-mono); font-size: 10px; }
.notes-list h3 { margin: 0; font-size: 15px; }
.notes-list p { max-width: 780px; margin: 6px 0 0; color: var(--public-muted); font-size: 12px; line-height: 1.7; }
.notes-list svg { color: var(--public-faint); }

.ask-boundary { position: relative; overflow: hidden; padding: 50px 0; border-bottom: 1px solid var(--public-line); background: #182019; color: #fff; }
.ask-boundary::before {
  position: absolute;
  top: 0;
  left: 0;
  width: 26%;
  height: 1px;
  background: linear-gradient(90deg, transparent, rgb(151 205 167 / 75%), transparent);
  content: '';
  pointer-events: none;
  animation: public-frame-glint 8.5s 1.4s ease-in-out infinite;
}
.ask-boundary__inner { display: grid; grid-template-columns: minmax(0, .9fr) minmax(0, 1.1fr) auto; align-items: center; gap: 54px; }
.ask-boundary__inner > div > span { color: #8fa99a; }
.ask-boundary h2 { margin: 7px 0 0; font-size: 25px; }
.ask-boundary p { max-width: 470px; margin: 9px 0 0; color: #b5c1b9; font-size: 12px; line-height: 1.7; }
.ask-boundary ul { display: grid; gap: 8px; margin: 0; padding: 0; color: #c5d0c8; font-size: 11px; list-style: none; }
.ask-boundary li { display: flex; align-items: center; gap: 8px; }
.ask-boundary li svg { color: #78aa87; }
.ask-boundary button { white-space: nowrap; }

.public-footer { background: #fff; }
.public-footer__inner { display: flex; align-items: center; justify-content: space-between; gap: 30px; min-height: 92px; }
.public-footer__inner > div:first-child { display: grid; gap: 3px; }
.public-footer strong { font-size: 13px; }
.public-footer span { color: var(--public-faint); font-size: 10px; }
.footer-links { display: flex; align-items: center; gap: 20px; }
.footer-links a,
.footer-links button { display: inline-flex; align-items: center; gap: 6px; padding: 0; border: 0; color: var(--public-muted); background: transparent; font-size: 11px; text-decoration: none; }
.footer-links a:hover,
.footer-links button:hover { color: var(--public-ink); }

.agent-layer { position: fixed; z-index: 40; inset: 0; }
.agent-scrim { position: absolute; inset: 0; width: 100%; border: 0; background: rgb(24 32 25 / 25%); }
.public-agent { position: absolute; top: 0; right: 0; display: grid; grid-template-rows: auto auto minmax(0, 1fr) auto auto auto; width: min(430px, 100%); height: 100dvh; border-left: 1px solid var(--public-line-strong); color: var(--public-ink); background: #fff; box-shadow: -18px 0 50px rgb(24 32 25 / 14%); }
.public-agent > header { display: flex; align-items: center; justify-content: space-between; min-height: 68px; padding: 0 18px; border-bottom: 1px solid var(--public-line); }
.agent-title { display: flex; align-items: center; gap: 10px; }
.agent-title > span:last-child { display: grid; gap: 2px; }
.agent-title strong { font-size: 13px; }
.agent-title small { color: var(--public-muted); font-size: 10px; }
.agent-mark { border-radius: 4px; }
.icon-button { display: grid; place-items: center; width: 34px; height: 34px; padding: 0; border: 1px solid var(--public-line); border-radius: 4px; color: var(--public-muted); background: #fff; }
.agent-boundary { display: flex; flex-wrap: wrap; gap: 7px; padding: 12px 18px; border-bottom: 1px solid var(--public-line); background: var(--public-band); }
.agent-boundary span { display: inline-flex; align-items: center; gap: 5px; color: var(--public-muted); font-size: 9px; }
.agent-boundary svg { color: var(--public-green); }
.agent-body { min-height: 0; overflow-y: auto; padding: 20px 18px; }
.agent-message { display: grid; justify-items: start; margin-bottom: 18px; }
.agent-message > span { margin-bottom: 5px; color: var(--public-faint); font-size: 9px; font-weight: 650; }
.agent-message p { max-width: 88%; margin: 0; padding: 10px 12px; border: 1px solid var(--public-line); border-radius: 4px; color: #405047; background: var(--public-band); font-size: 12px; line-height: 1.65; }
.agent-message.visitor { justify-items: end; }
.agent-message.visitor > span { text-align: right; }
.agent-message.visitor p { border-color: var(--public-green); color: #fff; background: var(--public-green); }
.agent-sources { display: grid; width: min(88%, 340px); margin-top: 7px; border: 1px solid var(--public-line); border-radius: 4px; background: #fff; }
.agent-sources > strong { padding: 9px 11px 6px; color: var(--public-faint); font-size: 9px; }
.agent-source { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 2px 8px; padding: 8px 11px; border: 0; border-top: 1px solid #edf1ee; color: var(--public-green); background: transparent; text-align: left; }
.agent-source > span { font-size: 11px; font-weight: 650; }
.agent-source small { grid-column: 1; color: var(--public-muted); font-size: 9px; line-height: 1.45; }
.agent-source svg { grid-row: 1 / span 2; grid-column: 2; align-self: center; }
.agent-source:hover { background: var(--public-band); }
.agent-prompts { display: grid; gap: 3px; padding: 14px 18px; border-top: 1px solid var(--public-line); }
.agent-prompts > span { margin-bottom: 3px; color: var(--public-faint); font-size: 9px; }
.agent-prompts button { padding: 5px 0; border: 0; color: #4f6e59; background: transparent; text-align: left; font-size: 11px; }
.agent-prompts button:hover { color: var(--public-ink); }
.agent-form { display: grid; grid-template-columns: minmax(0, 1fr) 34px; gap: 8px; align-items: end; margin: 0 14px; padding: 10px; border: 1px solid var(--public-line-strong); border-radius: 4px; background: #fff; }
.agent-form textarea { min-height: 58px; resize: none; border: 0; outline: 0; color: var(--public-ink); background: transparent; font-size: 12px; line-height: 1.55; }
.agent-form button { display: grid; place-items: center; width: 34px; height: 34px; border: 0; border-radius: 4px; color: #fff; background: var(--public-green); }
.agent-form button:disabled { color: #9aa89e; background: #e4ebe5; cursor: not-allowed; }
.agent-disclaimer { margin: 8px 18px 14px; color: var(--public-faint); font-family: var(--sage-font-mono); font-size: 9px; }

@keyframes public-copy-reveal {
  from { opacity: 0; transform: translateY(14px); filter: blur(5px); }
  to { opacity: 1; transform: translateY(0); filter: blur(0); }
}

@keyframes public-product-reveal {
  from { opacity: 0; transform: translateY(18px) scale(.992); filter: blur(6px); }
  to { opacity: 1; transform: translateY(0) scale(1); filter: blur(0); }
}

@keyframes public-title-glint {
  0%, 24% { background-position: 100% 50%; }
  54%, 100% { background-position: 0 50%; }
}

@keyframes public-signal-line {
  0%, 14% { opacity: 0; transform: translateX(-520%); }
  24%, 70% { opacity: 1; }
  82%, 100% { opacity: 0; transform: translateX(500%); }
}

@keyframes public-frame-glint {
  0%, 18% { opacity: 0; transform: translateX(-120%); }
  30%, 66% { opacity: 1; }
  78%, 100% { opacity: 0; transform: translateX(470%); }
}

@keyframes public-mark-glint {
  0%, 68% { opacity: 0; transform: skewX(-18deg) translateX(-260%); }
  74% { opacity: 1; }
  86%, 100% { opacity: 0; transform: skewX(-18deg) translateX(620%); }
}

@keyframes public-action-glint {
  from { transform: skewX(-18deg) translateX(-260%); }
  to { transform: skewX(-18deg) translateX(620%); }
}

@media (max-width: 1180px) {
  .section-inner,
  .public-header__inner,
  .hero-inner { width: min(100% - 64px, 1080px); }
  .hero-inner { grid-template-columns: minmax(0, .95fr) minmax(460px, 1.05fr); gap: 48px; }
  .hero-copy h1 { font-size: 54px; }
  .hero-positioning { font-size: 24px; }
  .section-heading { grid-template-columns: 118px minmax(0, 1fr) 250px; }
  .project-layout { gap: 48px; }
  .work-row { grid-template-columns: 45px 120px minmax(0, 1fr) 135px 22px; }
}

@media (max-width: 860px) {
  .public-header__inner { grid-template-columns: 1fr auto; }
  .public-header nav { display: none; }
  .hero-inner { grid-template-columns: 1fr; gap: 34px; min-height: auto; padding: 58px 0 62px; }
  .hero-copy { max-width: 680px; }
  .hero-product { width: 100%; max-width: 720px; }
  .public-section { padding: 78px 0; }
  .section-heading { grid-template-columns: 100px minmax(0, 1fr); align-items: start; }
  .section-heading > p { grid-column: 2; max-width: 480px; }
  .project-layout { grid-template-columns: 1fr; }
  .project-copy { max-width: 620px; }
  .work-row { grid-template-columns: 44px 112px minmax(0, 1fr) 22px; }
  .work-trace { display: none; }
  .milestone-list { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 38px 0; }
  .milestone-list li:nth-child(2)::after { display: none; }
  .ask-boundary__inner { grid-template-columns: 1fr auto; }
  .ask-boundary ul { grid-row: 2; grid-column: 1 / -1; }
}

@media (max-width: 560px) {
  .section-inner,
  .public-header__inner,
  .hero-inner { width: calc(100% - 36px); }
  .public-header__inner { min-height: 62px; }
  .public-brand > span:last-child { display: grid; gap: 0; }
  .public-brand strong { font-size: 12px; }
  .public-brand small { font-size: 9px; }
  .ask-sage { min-height: 34px; padding: 0 9px; font-size: 10px; }
  .hero-inner { gap: 24px; padding: 38px 0 28px; }
  .hero-copy h1 { max-width: 340px; font-size: 40px; line-height: 1.08; }
  .hero-positioning { margin-top: 10px; font-size: 19px; }
  .hero-support { margin-top: 15px; font-size: 12px; line-height: 1.7; }
  .hero-actions { gap: 16px; margin-top: 19px; }
  .primary-action { min-height: 35px; padding: 0 10px; font-size: 10px; }
  .hero-evidence { margin-top: 24px; }
  .hero-evidence__row { grid-template-columns: 31px minmax(0, 1fr) auto; gap: 8px; min-height: 65px; padding: 9px 0; }
  .hero-evidence__row > span:first-child { font-size: 15px; }
  .hero-evidence__row strong { font-size: 12px; }
  .hero-evidence__row strong em { display: block; margin: 2px 0 0; font-size: 9px; }
  .hero-evidence__row small { display: -webkit-box; overflow: hidden; font-size: 9px; line-height: 1.45; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
  .hero-product img { aspect-ratio: 16 / 7; border-radius: 4px; object-position: center top; }
  .hero-product figcaption { display: none; }
  .public-section { padding: 62px 0; }
  .project-section { padding-top: 16px; }
  .section-heading { display: grid; grid-template-columns: 1fr; gap: 9px; margin-bottom: 34px; }
  .section-heading > p { grid-column: 1; }
  .section-heading h2,
  .section-heading.compact h2 { font-size: 30px; }
  .project-layout { gap: 35px; }
  .project-copy h3 { font-size: 24px; }
  .project-copy > p { font-size: 12px; }
  .project-facts div { grid-template-columns: 72px minmax(0, 1fr); }
  .preview-bar { grid-template-columns: auto 1fr; }
  .preview-bar small { display: none; }
  .evidence-heading { align-items: flex-start; flex-direction: column; gap: 7px; margin-top: 58px; }
  .work-row { grid-template-columns: 34px minmax(0, 1fr) 20px; gap: 9px; min-height: 92px; }
  .work-main { display: grid; gap: 3px; }
  .work-main > span { font-size: 9px; }
  .work-main > strong { font-size: 14px; }
  .work-main > small { font-size: 9px; }
  .work-evidence { padding-left: 43px; }
  .work-evidence dl { grid-template-columns: 1fr; }
  .work-evidence dl > div,
  .work-evidence dl > div:nth-child(even) { padding: 14px 0; border-left: 0; }
  .work-evidence footer { align-items: flex-start; flex-direction: column; }
  .milestone-list { grid-template-columns: 1fr; gap: 0; }
  .milestone-list li { min-height: 108px; padding-right: 0; }
  .milestone-list li::after,
  .milestone-list li:nth-child(2)::after { top: 34px; bottom: 0; left: 17px; right: auto; display: block; width: 1px; height: auto; }
  .milestone-list li:last-child::after { display: none; }
  .milestone-list p { min-height: 0; }
  .notes-list article { grid-template-columns: 48px minmax(0, 1fr) auto; gap: 9px; }
  .notes-list article > span { grid-column: 2; grid-row: 1; }
  .notes-list article > div { grid-column: 2 / -1; }
  .notes-list article > svg { grid-column: 3; grid-row: 1; }
  .ask-boundary { padding: 42px 0; }
  .ask-boundary__inner { grid-template-columns: 1fr; gap: 24px; }
  .ask-boundary ul { grid-row: auto; grid-column: auto; }
  .ask-boundary button { justify-self: start; }
  .public-footer__inner { align-items: flex-start; flex-direction: column; justify-content: center; gap: 14px; min-height: 112px; }
  .public-agent { width: 100%; border-left: 0; }
}

@media (prefers-reduced-motion: reduce) {
  :global(html) { scroll-behavior: auto; }
  .public-header::after,
  .brand-mark::after,
  .hero-copy,
  .hero-copy h1 em,
  .hero-evidence,
  .hero-product,
  .hero-product::before,
  .product-preview::before,
  .ask-boundary::before,
  .ask-sage::after,
  .primary-action::after,
  .ask-boundary button::after { animation: none; }
  .hero-evidence__row > svg,
  .row-arrow { transition: none; }
}
</style>
