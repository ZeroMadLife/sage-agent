<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import {
  ArrowRight,
  ArrowUpRight,
  BookOpenText,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
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
import {
  answerPublicProfileQuestion,
  type PublicAgentReceipt,
  type PublicAgentSource,
  type PublicAgentStreamEvent,
} from '../harness/publicAgent'

const harnessWorkbenchImage = '/sage-harness-workbench.webp'

const productScreens = [
  {
    id: 'assistant',
    src: '/product/assistant.webp',
    label: 'Assistant',
    detail: '从目标进入统一工作台',
    alt: 'Sage Assistant 主页面真实界面，不含私人数据',
  },
  {
    id: 'knowledge',
    src: '/product/knowledge.webp',
    label: 'Knowledge',
    detail: '来源、社区与知识图谱',
    alt: 'Sage Knowledge 知识图谱真实界面，不含私人数据',
  },
  {
    id: 'harness',
    src: '/product/harness.webp',
    label: 'Harness Timeline',
    detail: '输入、模型、工具、审批与回答',
    alt: 'Sage Harness Timeline 真实执行界面，不含私人数据',
  },
] as const

type AgentMessage = {
  role: 'visitor' | 'sage'
  text: string
  sources?: PublicAgentSource[]
  receipt?: PublicAgentReceipt
  mode?: 'live' | 'fallback'
  notice?: string
  stage?: string
  streaming?: boolean
}

const drawerOpen = ref(false)
const question = ref('')
const activeSection = ref('home')
const activeWork = ref<string | null>(null)
const isAnswering = ref(false)
const motionReady = ref(false)
const scrollProgress = ref(0)
const activeProductIndex = ref(0)
const productGalleryPaused = ref(false)

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
  const reply = reactive<AgentMessage>({
    role: 'sage',
    text: '',
    stage: '连接受限公开 Agent',
    streaming: true,
  })
  agentMessages.value.push(reply)
  isAnswering.value = true
  try {
    const response = await answerPublicProfileQuestion(value, {
      onEvent: (event) => applyStreamEvent(reply, event),
    })
    reply.text = response.answer
    reply.sources = response.sources
    reply.receipt = response.receipt
    reply.mode = response.mode
    reply.notice = response.notice
  } finally {
    reply.stage = undefined
    reply.streaming = false
    isAnswering.value = false
  }
}

function applyStreamEvent(message: AgentMessage, event: PublicAgentStreamEvent) {
  if (event.type === 'stage') message.stage = event.label
  if (event.type === 'answer_delta') message.text += event.delta
  if (event.type === 'sources') message.sources = event.sources
  if (event.type === 'completed') message.receipt = event.receipt
  if (event.type === 'error') message.stage = event.message
}

function handleQuestionKeydown(event: KeyboardEvent) {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return
  event.preventDefault()
  void answerQuestion()
}

function inspectWork(id: string) {
  activeWork.value = id
  selectSection('work')
}

function toggleWork(id: string) {
  activeWork.value = activeWork.value === id ? null : id
}

function openSource(source: PublicAgentSource) {
  if (!source.target) return
  drawerOpen.value = false
  selectSection(source.target)
}

function productSlideClass(index: number) {
  const offset = (index - activeProductIndex.value + productScreens.length) % productScreens.length
  if (offset === 0) return 'is-active'
  if (offset === 1) return 'is-next'
  if (offset === productScreens.length - 1) return 'is-previous'
  return 'is-hidden'
}

function selectProduct(index: number) {
  activeProductIndex.value = index
}

function advanceProduct(offset: number) {
  activeProductIndex.value = (
    activeProductIndex.value + offset + productScreens.length
  ) % productScreens.length
}

let sectionObserver: IntersectionObserver | null = null
let revealObserver: IntersectionObserver | null = null
let scrollFrame: number | null = null
let productRotationTimer: number | null = null

function startProductRotation() {
  if (typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
  productRotationTimer = window.setInterval(() => {
    if (!productGalleryPaused.value) advanceProduct(1)
  }, 6000)
}

function updateScrollProgress() {
  if (scrollFrame !== null) return
  scrollFrame = window.requestAnimationFrame(() => {
    const scrollable = document.documentElement.scrollHeight - window.innerHeight
    scrollProgress.value = scrollable > 0 ? Math.min(window.scrollY / scrollable, 1) : 0
    scrollFrame = null
  })
}

onMounted(() => {
  window.addEventListener('scroll', updateScrollProgress, { passive: true })
  window.addEventListener('resize', updateScrollProgress)
  updateScrollProgress()

  if (typeof IntersectionObserver !== 'undefined') {
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

    revealObserver = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue
          entry.target.classList.add('is-visible')
          revealObserver?.unobserve(entry.target)
        }
      },
      { rootMargin: '0px 0px -10% 0px', threshold: 0.08 },
    )
    for (const element of document.querySelectorAll<HTMLElement>('[data-reveal]')) {
      revealObserver.observe(element)
    }
  }

  window.requestAnimationFrame(() => { motionReady.value = true })
  startProductRotation()
})

onBeforeUnmount(() => {
  sectionObserver?.disconnect()
  revealObserver?.disconnect()
  window.removeEventListener('scroll', updateScrollProgress)
  window.removeEventListener('resize', updateScrollProgress)
  if (scrollFrame !== null) window.cancelAnimationFrame(scrollFrame)
  if (productRotationTimer !== null) window.clearInterval(productRotationTimer)
})
</script>

<template>
  <div class="public-profile" :class="{ 'is-motion-ready': motionReady }">
    <header class="public-header">
      <span class="reading-progress" aria-hidden="true" :style="{ transform: `scaleX(${scrollProgress})` }"></span>
      <div class="public-header__inner">
        <button class="public-brand" type="button" @click="selectSection('home')">
          <span class="brand-mark"><Sparkles :size="15" /></span>
          <span><strong>ZeroMadLife</strong><small>/ Sage</small></span>
        </button>

        <nav aria-label="公开站点导航">
          <button type="button" data-section="work" :class="{ active: activeSection === 'work' }" :aria-current="activeSection === 'work' ? 'location' : undefined" @click="selectSection('work')">项目</button>
          <button type="button" data-section="path" :class="{ active: activeSection === 'path' }" :aria-current="activeSection === 'path' ? 'location' : undefined" @click="selectSection('path')">成长轨迹</button>
          <button type="button" data-section="writing" :class="{ active: activeSection === 'writing' }" :aria-current="activeSection === 'writing' ? 'location' : undefined" @click="selectSection('writing')">工程笔记</button>
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
            <p class="hero-kicker">PUBLIC ENGINEERING PORTFOLIO · 2026</p>
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

          <figure
            class="hero-product"
            aria-label="Sage 真实产品界面"
            aria-roledescription="轮播图"
            @mouseenter="productGalleryPaused = true"
            @mouseleave="productGalleryPaused = false"
            @focusin="productGalleryPaused = true"
            @focusout="productGalleryPaused = false"
          >
            <div class="hero-product__stage">
              <div
                v-for="(screen, index) in productScreens"
                :key="screen.id"
                class="hero-product__slide"
                :class="productSlideClass(index)"
                :data-product-slide="screen.id"
                :aria-label="index === activeProductIndex ? `${screen.label} 当前界面` : undefined"
                :aria-hidden="index !== activeProductIndex"
              >
                <img
                  :src="screen.src"
                  :alt="screen.alt"
                  :fetchpriority="index === 0 ? 'high' : 'auto'"
                />
              </div>
              <div class="hero-product__controls">
                <button type="button" aria-label="上一张产品界面" title="上一张" @click="advanceProduct(-1)">
                  <ChevronLeft :size="16" />
                </button>
                <button type="button" aria-label="下一张产品界面" title="下一张" @click="advanceProduct(1)">
                  <ChevronRight :size="16" />
                </button>
              </div>
            </div>
            <figcaption class="hero-product__rail">
              <span
                class="hero-product__cursor"
                aria-hidden="true"
                :style="{ transform: `translateX(${activeProductIndex * 100}%)` }"
              ></span>
              <button
                v-for="(screen, index) in productScreens"
                :key="screen.id"
                type="button"
                :class="{ active: index === activeProductIndex }"
                :data-product-picker="screen.id"
                :aria-pressed="index === activeProductIndex"
                @click="selectProduct(index)"
              >
                <span class="hero-product__thumb"><img :src="screen.src" alt="" /></span>
                <span><strong>{{ screen.label }}</strong><small>{{ screen.detail }}</small></span>
              </button>
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

          <div class="project-layout" data-reveal>
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
                <strong>Sage Harness / run timeline</strong>
                <small>真实界面 · 受控运行证据</small>
              </div>
              <img :src="harnessWorkbenchImage" alt="Sage Harness 执行阶段与回答时间线的真实界面" loading="lazy" />
              <figcaption><span>Run timeline</span><span>context · model · tools · approval · answer</span></figcaption>
            </figure>
          </div>

          <div class="evidence-heading" data-reveal>
            <span>ENGINEERING EVIDENCE</span>
            <strong>三条能追到代码与测试的判断</strong>
          </div>
          <div class="work-list" data-reveal>
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
              <Transition name="evidence-expand">
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
              </Transition>
            </div>
          </div>
        </div>
      </section>

      <section id="path" class="public-section path-section">
        <div class="section-inner">
          <header class="section-heading compact" data-reveal>
            <span>02 / LEARNING PATH</span>
            <h2>成长轨迹</h2>
            <p>只记录已经形成的工程切片，不把路线图当成交付。</p>
          </header>
          <ol class="milestone-list">
            <li v-for="(milestone, index) in milestones" :key="milestone.date + milestone.title" data-reveal :style="{ '--reveal-delay': `${index * 70}ms` }">
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
          <header class="section-heading compact" data-reveal>
            <span>03 / ENGINEERING NOTES</span>
            <h2>工程笔记</h2>
            <p>短记录保留关键判断；实现、测试与边界仍以仓库为准。</p>
          </header>
          <div class="notes-list">
            <article v-for="(note, index) in engineeringNotes" :key="note.date + note.title" data-reveal :style="{ '--reveal-delay': `${index * 55}ms` }">
              <time>{{ note.date }}</time>
              <span>{{ note.category }}</span>
              <div><h3>{{ note.title }}</h3><p>{{ note.text }}</p></div>
              <BookOpenText :size="16" />
            </article>
          </div>
        </div>
      </section>

      <section class="ask-boundary" aria-labelledby="ask-boundary-title">
        <div class="section-inner ask-boundary__inner" data-reveal>
          <div>
            <span>ASK SAGE</span>
            <h2 id="ask-boundary-title">受限公开资料问答</h2>
            <p>只依据审核发布的公开资料回答；服务不可用时会明确切换为本页资料回退。</p>
          </div>
          <ul>
            <li><FileCheck2 :size="15" />只回答已发布 corpus</li>
            <li><LockKeyhole :size="15" />不读取私人 Knowledge / Memory</li>
            <li><ShieldCheck :size="15" />独立 Public Agent，不连接私人应用</li>
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

    <Transition name="agent-drawer">
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
            <span><RotateCcw :size="13" />失败时透明回退</span>
          </div>

          <section class="agent-body" aria-live="polite">
            <div v-for="(message, index) in agentMessages" :key="index" class="agent-message" :class="message.role">
              <span>{{ message.role === 'sage' ? 'Sage' : '你' }}</span>
              <small v-if="message.stage" class="agent-stream-stage"><i></i>{{ message.stage }}</small>
              <p v-if="message.text" :class="{ 'is-streaming': message.streaming }">{{ message.text }}<i v-if="message.streaming" class="agent-stream-caret"></i></p>
              <p v-if="message.notice" class="agent-notice">{{ message.notice }}，以下为本页公开资料回退。</p>
              <p v-if="message.receipt" class="agent-receipt">
                资料包 {{ message.receipt.packageRevision }} · {{ message.receipt.requestId }}
              </p>
              <div v-if="message.sources?.length" class="agent-sources">
                <strong>回答依据</strong>
                <component
                  v-for="source in message.sources"
                  :key="source.id"
                  :is="source.url ? 'a' : 'button'"
                  :type="source.url ? undefined : 'button'"
                  :href="source.url"
                  :target="source.url ? '_blank' : undefined"
                  :rel="source.url ? 'noreferrer' : undefined"
                  class="agent-source"
                  :data-target="source.target || source.id"
                  @click="openSource(source)"
                >
                  <span>{{ source.label }}</span>
                  <small>{{ source.detail }}<template v-if="source.revision"> · {{ source.revision }}</template></small>
                  <ArrowRight :size="13" />
                </component>
              </div>
            </div>
          </section>

          <div class="agent-prompts">
            <span>HR 常问</span>
            <button type="button" @click="openAgent('请介绍一下这个项目')">这个项目解决什么问题？</button>
            <button type="button" @click="openAgent('项目使用了什么技术栈？')">项目使用了什么技术栈？</button>
            <button type="button" @click="openAgent('你在这个项目中做了什么？')">你在项目中做了什么？</button>
          </div>

          <form class="agent-form" :aria-busy="isAnswering" @submit.prevent="answerQuestion">
            <textarea v-model="question" rows="3" aria-label="询问公开资料" placeholder="询问项目、技术栈与工程实践…" :disabled="isAnswering" @keydown="handleQuestionKeydown"></textarea>
            <button type="submit" aria-label="发送问题" title="发送问题" :disabled="isAnswering || !question.trim()"><Send :size="16" /></button>
          </form>
          <p class="agent-disclaimer">versioned public corpus · no private session · same-origin API</p>
        </aside>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
:global(html) { scroll-behavior: smooth; }
:global(body) {
  background: #fff;
  text-rendering: optimizeLegibility;
  -webkit-font-smoothing: antialiased;
}

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
  --public-font-display: ui-serif, "Iowan Old Style", Baskerville, "Songti SC", STSong, serif;
  --public-font-body: "SF Pro Text", -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif;
  --public-font-mono: "SFMono-Regular", "Cascadia Code", "JetBrains Mono", Consolas, monospace;
  min-height: 100dvh;
  overflow-x: clip;
  color: var(--public-ink);
  background: #fff;
  font-family: var(--public-font-body);
  font-synthesis: none;
}

.public-profile button,
.public-profile textarea { font: inherit; }

.public-profile :is(button, a, textarea):focus-visible {
  outline: 2px solid #4c8a63;
  outline-offset: 3px;
}

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

.reading-progress {
  position: absolute;
  bottom: -1px;
  left: 0;
  width: 100%;
  height: 1px;
  background: var(--public-green);
  pointer-events: none;
  transform-origin: left center;
  transition: transform .12s linear;
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

.public-hero { border-bottom: 1px solid var(--public-line); }
.hero-inner {
  display: grid;
  grid-template-columns: minmax(0, .92fr) minmax(540px, 1.08fr);
  gap: 76px;
  align-items: center;
  min-height: 714px;
  padding: 48px 0 56px;
}
.hero-copy { min-width: 0; }
.hero-copy > * { animation: public-copy-reveal .68s cubic-bezier(.2, .72, .2, 1) both; }
.hero-copy > :nth-child(2) { animation-delay: .06s; }
.hero-copy > :nth-child(3) { animation-delay: .12s; }
.hero-copy > :nth-child(4) { animation-delay: .18s; }
.hero-copy > :nth-child(5) { animation-delay: .24s; }
.hero-copy > :nth-child(6) { animation-delay: .3s; }
.hero-kicker {
  margin: 0 0 18px;
  color: var(--public-green);
  font-family: var(--public-font-mono);
  font-size: 10px;
  font-weight: 650;
}
.hero-copy h1 {
  margin: 0;
  font-family: var(--public-font-display);
  font-size: 64px;
  font-weight: 600;
  line-height: 1.02;
  letter-spacing: 0;
}
.hero-copy h1 em {
  color: #5f8b69;
  font-style: normal;
  font-weight: 600;
}
.hero-positioning { margin: 15px 0 0; font-size: 26px; font-weight: 520; line-height: 1.3; letter-spacing: 0; }
.hero-support { max-width: 570px; margin: 22px 0 0; color: var(--public-muted); font-size: 16px; line-height: 1.8; }
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

.hero-evidence { margin-top: 34px; border-top: 1px solid var(--public-line); }
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
  background: linear-gradient(90deg, var(--public-green-soft) 0 3px, transparent 3px) no-repeat;
  background-size: 0 100%;
  text-align: left;
  transition: background-size .25s ease, padding .25s ease;
}
.hero-evidence__row > span:first-child { color: var(--evidence-accent); font-family: var(--public-font-mono); font-size: 19px; }
.hero-evidence__row > span:nth-child(2) { display: grid; gap: 4px; }
.hero-evidence__row strong { font-size: 14px; font-weight: 700; }
.hero-evidence__row strong em { margin-left: 7px; color: var(--evidence-accent); font-family: var(--public-font-mono); font-size: 11px; font-style: normal; font-weight: 500; }
.hero-evidence__row small { color: var(--public-muted); font-size: 12px; line-height: 1.6; }
.hero-evidence__row > svg { color: var(--public-faint); transition: transform .18s ease, color .18s ease; }
.hero-evidence__row:hover { padding-right: 8px; padding-left: 12px; background-size: 100% 100%; }
.hero-evidence__row:hover > svg { color: var(--evidence-accent); transform: translateX(3px); }

.hero-product {
  position: relative;
  align-self: stretch;
  display: flex;
  flex-direction: column;
  justify-content: center;
  min-width: 0;
  margin: 0;
  animation: public-product-reveal .82s .18s cubic-bezier(.2, .72, .2, 1) both;
}
.hero-product__stage {
  position: relative;
  z-index: 0;
  aspect-ratio: 16 / 9;
  perspective: 1100px;
  isolation: isolate;
}
.hero-product__slide {
  position: absolute;
  inset: 0;
  overflow: hidden;
  padding: 0;
  border: 1px solid var(--public-line-strong);
  border-radius: 5px 5px 0 0;
  background: #fff;
  box-shadow: 0 18px 42px rgb(38 68 48 / 9%);
  transform-origin: center bottom;
  transition: opacity .42s ease, transform .56s cubic-bezier(.2, .72, .2, 1), filter .42s ease;
}
.hero-product__slide.is-active { z-index: 3; opacity: 1; transform: translateZ(24px) scale(.94); }
.hero-product__slide.is-previous { z-index: 1; opacity: .72; filter: saturate(.72); transform: translateX(-30px) rotateY(4deg) rotateZ(-1.7deg) scale(.89); }
.hero-product__slide.is-next { z-index: 2; opacity: .82; filter: saturate(.82); transform: translateX(30px) rotateY(-4deg) rotateZ(1.7deg) scale(.89); }
.hero-product__slide.is-hidden { z-index: 0; opacity: 0; transform: scale(.86); pointer-events: none; }
.hero-product__slide > img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.hero-product__controls {
  position: absolute;
  z-index: 5;
  top: 18px;
  right: 28px;
  display: flex;
  gap: 7px;
}
.hero-product__controls button {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  padding: 0;
  border: 1px solid rgb(255 255 255 / 55%);
  border-radius: 4px;
  color: #fff;
  background: rgb(18 23 20 / 68%);
  box-shadow: 0 5px 16px rgb(10 18 12 / 18%);
  backdrop-filter: blur(8px);
}
.hero-product__controls button:hover { background: rgb(18 23 20 / 88%); }
.hero-product__rail {
  position: relative;
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  min-height: 60px;
  border: 1px solid var(--public-line-strong);
  border-top: 0;
  border-radius: 0 0 4px 4px;
  background: #fff;
}
.hero-product__cursor {
  position: absolute;
  z-index: 2;
  top: 0;
  left: 0;
  width: 33.3333%;
  height: 2px;
  background: var(--public-green);
  transition: transform .42s cubic-bezier(.2, .72, .2, 1);
}
.hero-product__rail > button {
  display: grid;
  grid-template-columns: 42px minmax(0, 1fr);
  align-items: center;
  gap: 9px;
  min-width: 0;
  padding: 8px 10px;
  border: 0;
  border-right: 1px solid var(--public-line);
  color: var(--public-faint);
  background: transparent;
  text-align: left;
  transition: color .2s ease, background .2s ease;
}
.hero-product__rail > button:last-child { border-right: 0; }
.hero-product__rail > button:hover,
.hero-product__rail > button.active { color: #344039; background: #f8faf8; }
.hero-product__thumb {
  display: block;
  overflow: hidden;
  aspect-ratio: 16 / 10;
  border: 1px solid var(--public-line);
  border-radius: 2px;
  background: var(--public-band);
}
.hero-product__thumb img { display: block; width: 100%; height: 100%; object-fit: cover; }
.hero-product__rail > button > span:last-child { display: grid; min-width: 0; gap: 2px; }
.hero-product__rail strong,
.hero-product__rail small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hero-product__rail strong { font-family: var(--public-font-body); font-size: 11px; }
.hero-product__rail small { font-family: var(--public-font-body); font-size: 9px; }

.public-section { scroll-margin-top: 68px; padding: 104px 0; border-bottom: 1px solid var(--public-line); }
.project-section { padding-top: 80px; }
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
  font-family: var(--public-font-mono);
  font-size: 11px;
}
.section-heading h2 {
  margin: 0;
  font-family: var(--public-font-display);
  font-size: 44px;
  font-weight: 600;
  line-height: 1.2;
  letter-spacing: 0;
}
.section-heading > p { margin: 0; color: var(--public-muted); font-size: 14px; line-height: 1.8; }
.section-heading.compact h2 { font-size: 38px; }

.project-layout {
  display: grid;
  grid-template-columns: minmax(320px, .72fr) minmax(0, 1.28fr);
  gap: 72px;
  align-items: center;
}
.project-copy { max-width: 470px; }
.project-name { display: inline-flex; align-items: center; gap: 7px; color: var(--public-green); font-size: 12px; font-weight: 650; }
.project-copy h3 { margin: 18px 0 14px; font-family: var(--public-font-display); font-size: 32px; font-weight: 600; line-height: 1.3; letter-spacing: 0; }
.project-copy > p { margin: 0; color: var(--public-muted); font-size: 15px; line-height: 1.85; }
.project-facts { margin: 30px 0 24px; border-top: 1px solid var(--public-line); }
.project-facts div { display: grid; grid-template-columns: 84px minmax(0, 1fr); gap: 14px; padding: 11px 0; border-bottom: 1px solid var(--public-line); }
.project-facts dt { color: var(--public-faint); font-size: 11px; }
.project-facts dd { margin: 0; color: #3f4b43; font-family: var(--public-font-mono); font-size: 11px; }

.product-preview { position: relative; min-width: 0; margin: 0; overflow: hidden; padding: 9px 9px 0; border: 1px solid var(--public-line-strong); border-radius: 4px; background: #fff; box-shadow: 0 18px 42px rgb(38 68 48 / 8%); }
.preview-bar { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: 12px; min-height: 32px; color: var(--public-faint); font-size: 10px; }
.preview-bar > span { display: flex; gap: 4px; }
.preview-bar i { width: 6px; height: 6px; border-radius: 50%; background: #b7c6bb; }
.preview-bar i:first-child { background: #b86b5c; }
.preview-bar i:nth-child(2) { background: #c49b46; }
.preview-bar i:nth-child(3) { background: #659374; }
.preview-bar strong { color: #465249; font-size: 11px; }
.product-preview img { display: block; width: 100%; aspect-ratio: 16 / 9; border: 1px solid #e4e9e5; object-fit: cover; }
.product-preview figcaption { display: flex; justify-content: space-between; gap: 12px; padding: 9px 1px 8px; color: var(--public-faint); font-family: var(--public-font-mono); font-size: 10px; }

.evidence-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 24px; margin-top: 86px; padding-bottom: 15px; border-bottom: 1px solid var(--public-line-strong); }
.evidence-heading strong { font-size: 14px; }
.work-list { border-bottom: 1px solid var(--public-line-strong); }
.work-entry { border-bottom: 1px solid var(--public-line); }
.work-entry:last-child { border-bottom: 0; }
.work-row {
  display: grid;
  grid-template-columns: 54px 150px minmax(0, 1fr) minmax(220px, .75fr) 150px 24px;
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
.work-index { color: var(--evidence-accent); font-family: var(--public-font-mono); font-size: 17px; }
.work-main { display: contents; }
.work-main > span { color: var(--public-muted); font-size: 12px; }
.work-main > strong { font-size: 18px; }
.work-main > small { color: var(--public-muted); font-size: 13px; line-height: 1.65; }
.work-trace { color: var(--evidence-accent); font-family: var(--public-font-mono); font-size: 11px; text-align: right; }
.row-arrow { color: var(--public-faint); transition: transform .18s ease, color .18s ease; }
.row-arrow.expanded { color: var(--evidence-accent); transform: rotate(180deg); }
.work-row:hover .row-arrow { color: var(--evidence-accent); }
.work-evidence { padding: 2px 0 30px 70px; }
.work-tags { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 14px; }
.work-tags span { padding: 4px 7px; border: 1px solid var(--public-line); border-radius: 3px; color: var(--public-muted); background: var(--public-band); font-family: var(--public-font-mono); font-size: 10px; }
.work-evidence dl { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); margin: 0; border-top: 1px solid var(--public-line); }
.work-evidence dl > div { padding: 17px 28px 17px 0; border-bottom: 1px solid var(--public-line); }
.work-evidence dl > div:nth-child(even) { padding-right: 0; padding-left: 28px; border-left: 1px solid var(--public-line); }
.work-evidence dt { color: var(--public-faint); font-size: 11px; font-weight: 650; }
.work-evidence dd { margin: 7px 0 0; color: #465249; font-size: 14px; line-height: 1.75; }
.work-evidence footer { display: flex; align-items: center; justify-content: space-between; gap: 18px; padding-top: 15px; }
.work-evidence footer a,
.work-evidence footer button { display: inline-flex; align-items: center; gap: 6px; padding: 0; border: 0; color: var(--public-green); background: transparent; font-size: 12px; font-weight: 650; text-decoration: none; }

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
.milestone-list time { color: var(--public-faint); font-family: var(--public-font-mono); font-size: 10px; }
.milestone-list strong { margin-top: 8px; font-size: 16px; }
.milestone-list p { min-height: 66px; margin: 7px 0 0; color: var(--public-muted); font-size: 13px; line-height: 1.7; }
.milestone-list small { margin-top: 9px; color: var(--public-green); font-size: 11px; }

.notes-list { border-top: 1px solid var(--public-line-strong); border-bottom: 1px solid var(--public-line-strong); }
.notes-list article { display: grid; grid-template-columns: 64px 130px minmax(0, 1fr) auto; align-items: start; gap: 22px; padding: 23px 0; border-bottom: 1px solid var(--public-line); }
.notes-list article:last-child { border-bottom: 0; }
.notes-list time { color: var(--public-faint); font-family: var(--public-font-mono); font-size: 11px; }
.notes-list article > span { color: var(--public-green); font-family: var(--public-font-mono); font-size: 11px; }
.notes-list h3 { margin: 0; font-size: 17px; }
.notes-list p { max-width: 780px; margin: 6px 0 0; color: var(--public-muted); font-size: 14px; line-height: 1.75; }
.notes-list svg { color: var(--public-faint); }

.ask-boundary { position: relative; overflow: hidden; padding: 50px 0; border-bottom: 1px solid var(--public-line); background: #182019; color: #fff; }
.ask-boundary__inner { display: grid; grid-template-columns: minmax(0, .9fr) minmax(0, 1.1fr) auto; align-items: center; gap: 54px; }
.ask-boundary__inner > div > span { color: #8fa99a; }
.ask-boundary h2 { margin: 7px 0 0; font-family: var(--public-font-display); font-size: 27px; font-weight: 600; }
.ask-boundary p { max-width: 470px; margin: 9px 0 0; color: #b5c1b9; font-size: 14px; line-height: 1.75; }
.ask-boundary ul { display: grid; gap: 8px; margin: 0; padding: 0; color: #c5d0c8; font-size: 12px; list-style: none; }
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
.agent-message p { max-width: 88%; margin: 0; padding: 10px 12px; border: 1px solid var(--public-line); border-radius: 4px; color: #405047; background: var(--public-band); font-size: 12px; line-height: 1.65; overflow-wrap: anywhere; white-space: pre-wrap; }
.agent-message.visitor { justify-items: end; }
.agent-message.visitor > span { text-align: right; }
.agent-message.visitor p { border-color: var(--public-green); color: #fff; background: var(--public-green); }
.agent-message .agent-notice { border-color: #e4c878; color: #725415; background: #fff9e7; font-size: 10px; }
.agent-message .agent-receipt { border: 0; padding: 0; color: var(--public-faint); background: transparent; font-family: var(--public-font-mono); font-size: 9px; }
.agent-stream-stage { display: inline-flex; align-items: center; gap: 6px; margin: 0 0 7px; color: var(--public-green); font-size: 10px; }
.agent-stream-stage i { width: 6px; height: 6px; border-radius: 50%; background: currentcolor; animation: agent-stage-pulse 1s ease-in-out infinite; }
.agent-stream-caret { display: inline-block; width: 1px; height: 1em; margin-left: 3px; background: var(--public-green); vertical-align: -2px; animation: agent-caret-blink .75s steps(1) infinite; }
.agent-sources { display: grid; width: min(88%, 340px); margin-top: 7px; border: 1px solid var(--public-line); border-radius: 4px; background: #fff; }
.agent-sources > strong { padding: 9px 11px 6px; color: var(--public-faint); font-size: 9px; }
.agent-source { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 2px 8px; padding: 8px 11px; border: 0; border-top: 1px solid #edf1ee; color: var(--public-green); background: transparent; text-align: left; }
.agent-source { text-decoration: none; }
.agent-source > span { font-size: 11px; font-weight: 650; }
.agent-source small { grid-column: 1; color: var(--public-muted); font-size: 9px; line-height: 1.45; }
.agent-source svg { grid-row: 1 / span 2; grid-column: 2; align-self: center; }
.agent-source:hover { background: var(--public-band); }

@keyframes agent-stage-pulse { 50% { opacity: .35; transform: scale(.72); } }
@keyframes agent-caret-blink { 50% { opacity: 0; } }
.agent-prompts { display: grid; gap: 3px; padding: 14px 18px; border-top: 1px solid var(--public-line); }
.agent-prompts > span { margin-bottom: 3px; color: var(--public-faint); font-size: 9px; }
.agent-prompts button { padding: 5px 0; border: 0; color: #4f6e59; background: transparent; text-align: left; font-size: 11px; }
.agent-prompts button:hover { color: var(--public-ink); }
.agent-form { display: grid; grid-template-columns: minmax(0, 1fr) 34px; gap: 8px; align-items: end; margin: 0 14px; padding: 10px; border: 1px solid var(--public-line-strong); border-radius: 4px; background: #fff; }
.agent-form textarea { min-height: 58px; resize: none; border: 0; outline: 0; color: var(--public-ink); background: transparent; font-size: 12px; line-height: 1.55; }
.agent-form button { display: grid; place-items: center; width: 34px; height: 34px; border: 0; border-radius: 4px; color: #fff; background: var(--public-green); }
.agent-form button:disabled { color: #9aa89e; background: #e4ebe5; cursor: not-allowed; }
.agent-disclaimer { margin: 8px 18px 14px; color: var(--public-faint); font-family: var(--public-font-mono); font-size: 9px; }

.is-motion-ready [data-reveal] {
  opacity: 0;
  transform: translateY(22px);
  transition: opacity .62s cubic-bezier(.2, .72, .2, 1), transform .62s cubic-bezier(.2, .72, .2, 1);
  transition-delay: var(--reveal-delay, 0ms);
}

.is-motion-ready [data-reveal].is-visible { opacity: 1; transform: translateY(0); }

.evidence-expand-enter-active,
.evidence-expand-leave-active { transition: opacity .2s ease, transform .2s ease; }
.evidence-expand-enter-from,
.evidence-expand-leave-to { opacity: 0; transform: translateY(-8px); }

.agent-drawer-enter-active,
.agent-drawer-leave-active { transition: opacity .24s ease; }
.agent-drawer-enter-active .public-agent,
.agent-drawer-leave-active .public-agent { transition: transform .3s cubic-bezier(.2, .72, .2, 1); }
.agent-drawer-enter-from,
.agent-drawer-leave-to { opacity: 0; }
.agent-drawer-enter-from .public-agent,
.agent-drawer-leave-to .public-agent { transform: translateX(100%); }

@keyframes public-copy-reveal {
  from { opacity: 0; transform: translateY(16px); }
  to { opacity: 1; transform: translateY(0); }
}

@keyframes public-product-reveal {
  from { opacity: 0; transform: translateY(20px) scale(.985); }
  to { opacity: 1; transform: translateY(0) scale(1); }
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
  .work-row { grid-template-columns: 45px 120px minmax(0, 1fr) minmax(190px, .75fr) 120px 22px; }
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
  .work-row { grid-template-columns: 44px 112px minmax(0, 1fr) minmax(180px, .85fr) 22px; }
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
  .hero-inner { gap: 14px; padding: 24px 0 10px; }
  .hero-kicker { margin-bottom: 10px; font-size: 9px; }
  .hero-copy h1 { max-width: 350px; font-size: 40px; line-height: 1.06; }
  .hero-positioning { margin-top: 11px; font-size: 19px; }
  .hero-support { margin-top: 12px; font-size: 13px; line-height: 1.65; }
  .hero-actions { gap: 16px; margin-top: 15px; }
  .primary-action { min-height: 37px; padding: 0 11px; font-size: 11px; }
  .hero-evidence { margin-top: 20px; }
  .hero-evidence__row { grid-template-columns: 31px minmax(0, 1fr) auto; gap: 8px; min-height: 56px; padding: 5px 0; }
  .hero-evidence__row > span:first-child { font-size: 15px; }
  .hero-evidence__row strong { font-size: 13px; }
  .hero-evidence__row strong em { display: block; margin: 2px 0 0; font-size: 10px; }
  .hero-evidence__row small { display: -webkit-box; overflow: hidden; font-size: 11px; line-height: 1.5; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
  .hero-product__stage { aspect-ratio: 16 / 5.1; }
  .hero-product__slide.is-active { transform: scale(.96); }
  .hero-product__slide.is-previous { transform: translateX(-12px) rotateZ(-1deg) scale(.92); }
  .hero-product__slide.is-next { transform: translateX(12px) rotateZ(1deg) scale(.92); }
  .hero-product__slide > img { object-position: center top; }
  .hero-product__controls { top: 11px; right: 17px; }
  .hero-product__controls button { width: 29px; height: 29px; }
  .hero-product__rail { min-height: 38px; }
  .hero-product__rail > button { display: grid; grid-template-columns: minmax(0, 1fr); padding: 5px 3px; text-align: center; }
  .hero-product__thumb { display: none; }
  .hero-product__rail small { display: none; }
  .hero-product__rail strong { font-size: 9px; }
  .public-section { padding: 62px 0; }
  .project-section { padding-top: 16px; }
  .section-heading { display: grid; grid-template-columns: 1fr; gap: 9px; margin-bottom: 34px; }
  .section-heading > p { grid-column: 1; }
  .section-heading h2,
  .section-heading.compact h2 { font-size: 30px; }
  .project-layout { gap: 35px; }
  .project-copy h3 { font-size: 24px; }
  .project-copy > p { font-size: 14px; }
  .project-facts div { grid-template-columns: 72px minmax(0, 1fr); }
  .preview-bar { grid-template-columns: auto 1fr; }
  .preview-bar small { display: none; }
  .evidence-heading { align-items: flex-start; flex-direction: column; gap: 7px; margin-top: 58px; }
  .work-row { grid-template-columns: 34px minmax(0, 1fr) 20px; gap: 9px; min-height: 92px; }
  .work-main { display: grid; gap: 3px; }
  .work-main > span { font-size: 10px; }
  .work-main > strong { font-size: 15px; }
  .work-main > small { font-size: 11px; }
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
  .hero-copy > *,
  .hero-product,
  .agent-stream-stage i,
  .agent-stream-caret { animation: none; }
  .is-motion-ready [data-reveal] { opacity: 1; transform: none; transition: none; }
  .reading-progress,
  .hero-evidence__row,
  .hero-product__slide,
  .hero-product__cursor,
  .hero-product__rail > button,
  .evidence-expand-enter-active,
  .evidence-expand-leave-active,
  .agent-drawer-enter-active,
  .agent-drawer-leave-active,
  .agent-drawer-enter-active .public-agent,
  .agent-drawer-leave-active .public-agent { transition: none; }
  .hero-evidence__row > svg,
  .row-arrow { transition: none; }
  .hero-product__slide.is-active { transform: none; }
  .hero-product__slide.is-previous,
  .hero-product__slide.is-next { opacity: 0; transform: none; }
}
</style>
