<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  ArrowRight,
  ArrowUpRight,
  Check,
  ChevronDown,
  CircleDot,
  ExternalLink,
  Github,
  GitBranch,
  MessageCircle,
  Network,
  Send,
  Sparkles,
  X,
} from 'lucide-vue-next'
import { useRoute } from 'vue-router'
import { useMarkdown } from '../composables/useMarkdown'
import knowledgeWorkbenchImage from '../assets/public/sage-knowledge-workbench.jpg'
import {
  answerPublicProfileQuestion,
  type PublicAgentSource,
} from '../harness/publicAgent'
import { loadPublishingDraft } from '../harness/publishingDraft'

type FlowStage = {
  id: string
  label: string
  detail: string
  color: string
}

type AgentMessage = {
  role: 'visitor' | 'sage'
  text: string
  sources?: PublicAgentSource[]
}

const route = useRoute()
const drawerOpen = ref(false)
const question = ref('')
const activeSection = ref('home')
const activeFlow = ref('purpose')
const activeWork = ref<string | null>(null)
const isAnswering = ref(false)
const draft = loadPublishingDraft()
const previewingDraft = computed(() => route.query.preview === 'draft')
const { render } = useMarkdown()
const draftBody = computed(() => render(draft.body))

const flowStages: FlowStage[] = [
  { id: 'purpose', label: 'Purpose', detail: '先说清楚今天要解决的目标。', color: '#2f704e' },
  { id: 'knowledge', label: 'Knowledge', detail: '从自己的 Wiki 和来源里找已有证据。', color: '#4f789d' },
  { id: 'practice', label: 'Practice', detail: '用主对话规划练习、任务和必要的 Coding。', color: '#c28d34' },
  { id: 'evidence', label: 'Evidence', detail: '保留 timeline、引用和可验证结果。', color: '#c87566' },
  { id: 'growth', label: 'Growth', detail: '经确认后，把增量知识沉淀回系统。', color: '#6b6b9e' },
]

const workItems = [
  {
    id: 'sage',
    index: '01',
    title: 'Sage / Personal AI Learning Companion',
    description: '把目标、个人知识、Web 证据、实践和长期沉淀放进一个可恢复的学习闭环。',
    tags: ['Vue 3', 'FastAPI', 'Knowledge Graph'],
    reason: '通用聊天不会持续理解用户正在形成什么能力，也无法说明一次回答如何进入长期知识。',
    decision: '把主对话设为目标驱动入口；Knowledge 负责结构和治理，Coding 只在需要实践时被调用。',
    evidence: '公开源码同时包含 Assistant、Knowledge、Practice、durable timeline 与 proposal 审批路径，可以逐项追溯。',
    boundary: 'Learning Goal、Mastery Ledger 与公网只读 Agent 仍在建设，不把规划描述成已经交付。',
    question: 'Sage 为什么不是一个普通聊天机器人？',
    linkLabel: '查看 Sage 仓库',
    href: 'https://github.com/ZeroMadLife/sage-agent',
  },
  {
    id: 'harness',
    index: '02',
    title: 'Chat Harness 2.0',
    description: '同一套 Harness 服务 Assistant、Knowledge 和 Practice，所有阶段来自真实 timeline。',
    tags: ['LangGraph', 'Durable Timeline', 'Approval'],
    reason: 'Agent 刷新、断线和高风险工具执行如果缺少统一事实源，界面很容易显示无法证明的状态。',
    decision: '以 Sage timeline 作为 UI 与审计事实源，用 Surface Adapter 区分页面上下文和能力，不复制运行时。',
    evidence: 'deerflow_v2 已是新会话默认运行时；Tool、MCP、Skills、Subagent、审批和恢复共用同一条 Harness 路径。',
    boundary: 'Goal/Mastery、长期 Memory 检索与 Knowledge 增量闭环仍按 H2.7-H2.9 分阶段交付。',
    question: 'Harness 2.0 如何保证刷新后还能恢复？',
    linkLabel: '查看 Harness package',
    href: 'https://github.com/ZeroMadLife/sage-agent/tree/dev/sage-v7/packages/sage_harness',
  },
  {
    id: 'knowledge',
    index: '03',
    title: 'Knowledge as a surface',
    description: '图谱只负责看清知识结构，主对话负责行动；节点、页面和 revision 作为提问上下文。',
    tags: ['Sigma', 'Graphology', 'RAG'],
    reason: '把知识图谱做成第二个聊天入口会增加认知负担，也会让选中节点和真实检索上下文互相混淆。',
    decision: '图谱保持 Obsidian 式探索；提问发生在共享 Chat Dock，提交时冻结 node、page 与 revision。',
    evidence: '当前前端复用 Sigma、Graphology 和 ForceAtlas2，并对 hover、selection、社区与大图降级分别建模。',
    boundary: '5k 节点的社区聚合仍需要服务端投影；当前不会用前端假数据伪装完整大图能力。',
    question: '知识图谱为什么不承担主要对话？',
    linkLabel: '查看 Knowledge 前端',
    href: 'https://github.com/ZeroMadLife/sage-agent/tree/dev/sage-v7/frontend/src/components/knowledge',
  },
]

const fieldNotes = [
  { date: '07.20', text: '公开门面应该展示真实作品，不应该伪装成完整的博客平台。' },
  { date: '07.19', text: '主对话负责行动，知识库负责观察，成长站负责把验证过的过程公开。' },
  { date: '07.18', text: '恢复机制的难点不是重连，而是证明哪些副作用已经发生。' },
  { date: '07.16', text: '只有能支撑提问、练习和验证的标签，才值得成为知识节点。' },
]

const milestones = [
  { date: '2026 · 07', title: 'Reliable Agent Harness', detail: '恢复、审批、持久 Timeline 与知识提案进入同一条运行路径。', state: 'now' },
  { date: '2026 · 06', title: 'Personal Knowledge Base', detail: '来源、Wiki、检索、图谱和 revision 形成可追溯结构。', state: 'done' },
  { date: '2026 · 04', title: 'Retrieval Practice', detail: '从 RAG 实验走向可评估、可引用的实际系统。', state: 'done' },
]

const agentMessages = ref<AgentMessage[]>([
  { role: 'sage', text: '这是公开资料预览。我只回答这页已经公开的项目、方法和成长记录。' },
])

function selectSection(section: string) {
  activeSection.value = section
  const reduceMotion = typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  if (section === 'home') {
    window.scrollTo?.({ top: 0, behavior: reduceMotion ? 'auto' : 'smooth' })
    return
  }
  const target = document.getElementById(section)
  target?.scrollIntoView?.({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'start' })
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

function setFlow(stage: FlowStage) {
  activeFlow.value = stage.id
}

function toggleWork(id: string) {
  activeWork.value = activeWork.value === id ? null : id
}

function openSource(source: PublicAgentSource) {
  drawerOpen.value = false
  selectSection(source.target)
}
</script>

<template>
  <div class="public-profile">
    <header class="public-header">
      <button class="public-brand" type="button" @click="selectSection('home')">
        <span class="brand-mark"><Sparkles :size="15" /></span>
        <span><strong>ZeroMadLife</strong><small>building with Sage</small></span>
      </button>

      <nav aria-label="公开站点导航">
        <button type="button" data-section="work" :class="{ active: activeSection === 'work' }" @click="selectSection('work')">项目</button>
        <button type="button" data-section="writing" :class="{ active: activeSection === 'writing' }" @click="selectSection('writing')">笔记</button>
        <button type="button" data-section="path" :class="{ active: activeSection === 'path' }" @click="selectSection('path')">轨迹</button>
        <button type="button" data-section="about" :class="{ active: activeSection === 'about' }" @click="selectSection('about')">关于</button>
      </nav>

      <button class="ask-sage" type="button" @click="openAgent()"><MessageCircle :size="15" />问 Sage</button>
    </header>

    <main>
      <article v-if="previewingDraft" class="draft-preview">
        <RouterLink class="back-link" to="/public"><ArrowRight :size="15" class="back-icon" />返回公开主页</RouterLink>
        <p class="eyebrow">PUBLIC DRAFT</p>
        <h1>{{ draft.title }}</h1>
        <strong>{{ draft.summary }}</strong>
        <div class="message-content" v-html="draftBody"></div>
      </article>

      <template v-else>
        <section id="top" class="public-hero">
          <div class="hero-copy">
            <p class="hero-location">AI systems · knowledge engineering · product practice</p>
            <h1>ZeroMadLife</h1>
            <p class="hero-lede">我在构建一个能陪人持续学习、实践和沉淀的个人 AI 系统。</p>
            <p class="hero-support">Sage 把目标、个人知识和真实证据连成一条可以回放的成长路径。这里是项目现场，不是一个包装好的简历。</p>
            <div class="hero-actions">
              <button class="primary-action" type="button" @click="selectSection('work')">看 Sage <ArrowRight :size="15" /></button>
              <button class="text-action" type="button" @click="openAgent('Sage 是做什么的？')">向公开资料提问 <MessageCircle :size="15" /></button>
            </div>
            <div class="hero-signals" aria-label="公开项目状态">
              <span><CircleDot :size="13" />持续构建</span>
              <span><GitBranch :size="13" />dev/sage-v7</span>
              <span><Check :size="13" />可追溯实践</span>
            </div>
          </div>

          <div class="hero-system" aria-label="Sage 学习闭环">
            <div class="system-heading"><span>THE LOOP</span><strong>一个目标，五个阶段</strong></div>
            <div class="system-flow">
              <button
                v-for="(stage, index) in flowStages"
                :key="stage.id"
                class="flow-stage"
                :class="{ active: activeFlow === stage.id }"
                type="button"
                @mouseenter="setFlow(stage)"
                @focus="setFlow(stage)"
                @click="setFlow(stage)"
              >
                <span class="flow-node" :style="{ '--flow-color': stage.color }"><span>{{ String(index + 1).padStart(2, '0') }}</span></span>
                <strong>{{ stage.label }}</strong>
                <small>{{ stage.detail }}</small>
                <i v-if="index < flowStages.length - 1" class="flow-connector" aria-hidden="true"></i>
              </button>
            </div>
            <div class="system-foot"><span>current focus</span><strong>{{ flowStages.find((stage) => stage.id === activeFlow)?.label }}</strong><small>{{ flowStages.find((stage) => stage.id === activeFlow)?.detail }}</small></div>
          </div>
        </section>

        <section class="proof-strip" aria-label="Sage 项目说明">
          <span class="proof-label">CURRENT PROJECT</span>
          <strong>Sage / Personal AI Learning Companion</strong>
          <span>目标驱动的对话 · 可审计的知识 · 可恢复的实践</span>
          <a href="https://github.com/ZeroMadLife/sage-agent" target="_blank" rel="noreferrer" aria-label="在 GitHub 查看 Sage"><Github :size="16" /><ArrowUpRight :size="14" /></a>
        </section>

        <section id="work" class="public-section feature-section">
          <div class="section-heading">
            <div><span class="eyebrow">01 / PROJECT</span><h2>把学习做成一个可运行的系统</h2></div>
            <p>面试官可以先看真实工作台，再读背后的取舍。</p>
          </div>
          <div class="feature-grid">
            <div class="feature-copy">
              <span class="project-kicker"><Network :size="15" /> Sage</span>
              <h3>主对话负责行动，Knowledge 负责看清。</h3>
              <p>用户设定目标后，Sage 会检索已有知识，用 Web/MCP 补充证据，安排提问或练习，并把经过确认的增量内容沉淀回 Wiki。</p>
              <dl class="feature-facts">
                <div><dt>入口</dt><dd>Goal-driven Chat</dd></div>
                <div><dt>运行时</dt><dd>Harness 2.0 / LangGraph</dd></div>
                <div><dt>原则</dt><dd>Evidence before memory</dd></div>
              </dl>
              <a class="inline-link" href="https://github.com/ZeroMadLife/sage-agent" target="_blank" rel="noreferrer">查看源码与设计 <ExternalLink :size="14" /></a>
            </div>
            <figure class="product-preview">
              <div class="preview-bar"><span><i></i><i></i><i></i></span><strong>Sage Knowledge / graph view</strong><small>真实工作台截面</small></div>
              <img :src="knowledgeWorkbenchImage" alt="Sage Knowledge 图谱工作台的真实界面截面" loading="lazy" />
              <figcaption><span>Knowledge Graph</span><span>graph revision bound</span></figcaption>
            </figure>
          </div>
        </section>

        <section id="writing" class="public-section writing-section">
          <div class="section-heading">
            <div><span class="eyebrow">02 / SELECTED WORK</span><h2>正在写的，不只是文章</h2></div>
            <p>每一项都对应代码、设计或一次真实的工程判断。</p>
          </div>
          <div class="work-list">
            <div v-for="item in workItems" :key="item.id" class="work-entry">
              <button
                class="work-row"
                type="button"
                :data-work-id="item.id"
                :aria-expanded="activeWork === item.id"
                :aria-controls="`work-evidence-${item.id}`"
                @click="toggleWork(item.id)"
              >
                <span class="work-index">{{ item.index }}</span>
                <span class="work-main"><strong>{{ item.title }}</strong><small>{{ item.description }}</small><span class="work-tags"><em v-for="tag in item.tags" :key="tag">{{ tag }}</em></span></span>
                <ChevronDown :size="17" class="row-arrow" :class="{ expanded: activeWork === item.id }" />
              </button>
              <section
                v-if="activeWork === item.id"
                :id="`work-evidence-${item.id}`"
                class="work-evidence"
                :data-work-evidence="item.id"
              >
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
          <div class="notes-heading"><span class="eyebrow">FIELD NOTES</span><strong>最近的几次想法</strong></div>
          <div class="notes-list">
            <article v-for="note in fieldNotes" :key="note.date + note.text"><time>{{ note.date }}</time><p>{{ note.text }}</p></article>
          </div>
        </section>

        <section id="path" class="public-section path-section">
          <div class="section-heading">
            <div><span class="eyebrow">03 / LEARNING PATH</span><h2>成长记录，公开过程而不是结论</h2></div>
            <p>系统里的每个“完成”都应该有对应的证据。</p>
          </div>
          <ol class="milestone-list">
            <li v-for="milestone in milestones" :key="milestone.title" :class="milestone.state">
              <span class="milestone-mark"><Check v-if="milestone.state === 'done'" :size="14" /><CircleDot v-else :size="14" /></span>
              <div><time>{{ milestone.date }}</time><strong>{{ milestone.title }}</strong><p>{{ milestone.detail }}</p></div>
            </li>
          </ol>
        </section>

        <section id="about" class="public-section about-section">
          <div class="about-copy"><span class="eyebrow">04 / ABOUT THE WORK</span><h2>我更关心系统如何陪人变好。</h2><p>不是让 Agent 看起来更像一个人，而是让它知道什么时候检索、什么时候提问、什么时候停下来等待确认。</p><p>这也是 Sage 的核心：把个人知识变成有上下文的长期协作，而不是把聊天记录堆成另一个收件箱。</p></div>
          <div class="about-aside"><div><span>OPEN SOURCE</span><strong>可阅读的实现</strong><small>代码、设计文档和验收手册都在仓库里。</small></div><div><span>PUBLIC SURFACE</span><strong>有限但诚实</strong><small>公开问答只覆盖已经发布的资料，不伪装成完整 Agent。</small></div><a class="inline-link" href="https://github.com/ZeroMadLife/sage-agent" target="_blank" rel="noreferrer"><Github :size="15" />打开 GitHub <ArrowUpRight :size="14" /></a></div>
        </section>
      </template>
    </main>

    <footer class="public-footer"><div><strong>ZeroMadLife / Sage</strong><span>记录真实学习，发布可验证作品。</span></div><div class="footer-links"><a href="https://github.com/ZeroMadLife/sage-agent" target="_blank" rel="noreferrer"><Github :size="14" />GitHub</a><button type="button" @click="openAgent()"><MessageCircle :size="14" />问 Sage</button></div></footer>

    <aside v-if="drawerOpen" class="public-agent" aria-label="公开 Sage 助手">
      <header><div class="agent-title"><span class="agent-mark"><Sparkles :size="16" /></span><span><strong>Ask Sage</strong><small>公开资料预览</small></span></div><button class="icon-button" type="button" aria-label="关闭公开助手" title="关闭" @click="drawerOpen = false"><X :size="17" /></button></header>
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
            ><span>{{ source.label }}</span><small>{{ source.detail }}</small><ArrowRight :size="13" /></button>
          </div>
        </div>
      </section>
      <div class="agent-prompts"><span>试试问：</span><button type="button" @click="openAgent('Sage 是做什么的？')">Sage 是做什么的？</button><button type="button" @click="openAgent('Harness 2.0 解决什么问题？')">Harness 2.0 解决什么问题？</button><button type="button" @click="openAgent('知识图谱在这里做什么？')">知识图谱在这里做什么？</button></div>
      <form class="agent-form" :aria-busy="isAnswering" @submit.prevent="answerQuestion"><textarea v-model="question" rows="3" placeholder="询问公开的项目与方法…" :disabled="isAnswering"></textarea><button type="submit" aria-label="发送问题" title="发送问题" :disabled="isAnswering || !question.trim()"><Send :size="16" /></button></form>
      <p class="agent-disclaimer">当前是静态资料问答，后续可替换为受限公网 Harness。</p>
    </aside>
  </div>
</template>

<style scoped>
:global(html) { scroll-behavior: smooth; }
.public-profile { min-height: 100dvh; color: #1a201c; background: #f8faf8; font-family: var(--sage-font-sans); overflow: hidden; }
.public-profile button,.public-profile textarea { font: inherit; }
.public-header { position: relative; z-index: 3; display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; width: min(1240px, calc(100% - 64px)); min-height: 78px; margin: 0 auto; border-bottom: 1px solid #dfe6e0; }
.public-brand { display: inline-flex; align-items: center; justify-self: start; gap: 10px; padding: 0; border: 0; color: #1a201c; background: transparent; text-decoration: none; cursor: pointer; }
.public-brand > span:last-child { display: grid; gap: 1px; }.public-brand strong { font-size: 14px; letter-spacing: 0; }.public-brand small { color: #76847a; font-size: 10px; letter-spacing: 0; }.brand-mark,.agent-mark { display: grid; place-items: center; width: 30px; height: 30px; color: #fff; background: #2f704e; }.brand-mark { border-radius: 50%; }
.public-header nav { display: flex; gap: 27px; }.public-header nav button { min-height: 35px; padding: 7px 0 0; border: 0; border-bottom: 1px solid transparent; color: #637067; background: transparent; font-size: 12px; cursor: pointer; }.public-header nav button:hover,.public-header nav button.active { color: #1a201c; border-bottom-color: #2f704e; }
.ask-sage,.primary-action { display: inline-flex; align-items: center; justify-content: center; gap: 7px; min-height: 36px; padding: 0 12px; border: 1px solid #2f704e; color: #fff; background: #2f704e; font-size: 12px; text-decoration: none; cursor: pointer; }.ask-sage { justify-self: end; border-color: #cbd8cd; color: #28543c; background: #eef5ef; }.ask-sage:hover,.primary-action:hover { background: #255d41; }
.public-profile main { width: min(1240px, calc(100% - 64px)); margin: 0 auto; }.public-hero { display: grid; grid-template-columns: minmax(0, 1.02fr) minmax(440px, .98fr); gap: clamp(48px, 8vw, 132px); align-items: center; min-height: min(700px, calc(100svh - 78px)); padding: 68px 0 72px; }.hero-copy { max-width: 600px; }.hero-location,.eyebrow,.system-heading span,.proof-label { color: #7c8c81; font-size: 10px; letter-spacing: 0; text-transform: uppercase; }.hero-location { margin: 0 0 20px; }.hero-copy h1 { margin: 0; font-size: 82px; letter-spacing: 0; line-height: .98; }.hero-lede { max-width: 520px; margin: 27px 0 0; font-size: 31px; line-height: 1.3; letter-spacing: 0; }.hero-support { max-width: 520px; margin: 19px 0 0; color: #6b796f; font-size: 14px; line-height: 1.8; }.hero-actions { display: flex; align-items: center; gap: 23px; margin-top: 28px; }.text-action { display: inline-flex; align-items: center; gap: 6px; padding: 0; border: 0; color: #2f704e; background: transparent; font-size: 12px; }.text-action:hover { color: #1a201c; }.hero-signals { display: flex; flex-wrap: wrap; gap: 18px; margin-top: 42px; color: #7b887f; font-size: 11px; }.hero-signals span { display: inline-flex; align-items: center; gap: 5px; }.hero-signals svg { color: #2f704e; }
.hero-system { align-self: stretch; display: flex; flex-direction: column; justify-content: center; min-width: 0; padding: 30px 0 30px 34px; border-left: 1px solid #dce6df; }.system-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 20px; padding-bottom: 22px; border-bottom: 1px solid #dce6df; }.system-heading strong { font-size: 13px; font-weight: 600; }.system-flow { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 0; padding: 38px 0 32px; }.flow-stage { position: relative; display: grid; justify-items: start; align-content: start; min-width: 0; padding: 0 12px 0 0; border: 0; color: #748278; background: transparent; text-align: left; }.flow-stage:first-child { padding-left: 0; }.flow-node { display: grid; place-items: center; width: 43px; height: 43px; border: 1px solid #b9c8bc; border-radius: 50%; color: var(--flow-color); background: #f8faf8; transition: color .2s ease, border-color .2s ease, box-shadow .2s ease, background .2s ease; }.flow-node span { font-family: var(--sage-font-mono); font-size: 10px; }.flow-stage strong { margin-top: 14px; color: #6f7e73; font-size: 12px; font-weight: 600; }.flow-stage small { max-width: 116px; margin-top: 6px; color: #93a096; font-size: 10px; line-height: 1.55; }.flow-stage.active .flow-node { border-color: var(--flow-color); background: color-mix(in srgb, var(--flow-color) 10%, #f8faf8); box-shadow: 0 0 0 5px color-mix(in srgb, var(--flow-color) 14%, transparent); }.flow-stage.active strong { color: #1a201c; }.flow-connector { position: absolute; top: 21px; left: 43px; width: calc(100% - 43px); height: 1px; background: #cbd8cd; }.system-foot { display: grid; grid-template-columns: auto auto minmax(0, 1fr); align-items: baseline; gap: 10px; padding-top: 16px; border-top: 1px solid #dce6df; }.system-foot span { color: #92a096; font-size: 10px; text-transform: uppercase; }.system-foot strong { color: #2f704e; font-size: 12px; }.system-foot small { color: #76847a; font-size: 11px; }
.proof-strip { display: grid; grid-template-columns: auto minmax(0, 1fr) auto auto; align-items: center; gap: 18px; min-height: 68px; padding: 0 18px; border-top: 1px solid #dfe6e0; border-bottom: 1px solid #dfe6e0; }.proof-strip strong { font-size: 13px; }.proof-strip > span:not(.proof-label) { color: #728077; font-size: 11px; }.proof-strip > a { display: inline-flex; align-items: center; gap: 6px; color: #2f704e; text-decoration: none; }.proof-strip > a:hover { color: #1a201c; }
.public-section { padding: 95px 0 104px; border-bottom: 1px solid #dfe6e0; }.section-heading { display: flex; align-items: end; justify-content: space-between; gap: 30px; margin-bottom: 44px; }.section-heading h2 { max-width: 590px; margin: 10px 0 0; font-size: 41px; letter-spacing: 0; line-height: 1.15; }.section-heading p { max-width: 260px; margin: 0; color: #728077; font-size: 12px; line-height: 1.7; }
.feature-grid { display: grid; grid-template-columns: minmax(0, .82fr) minmax(0, 1.18fr); gap: 60px; align-items: center; }.feature-copy { max-width: 460px; }.project-kicker { display: inline-flex; align-items: center; gap: 6px; color: #2f704e; font-size: 12px; }.feature-copy h3 { margin: 18px 0 13px; font-size: 29px; letter-spacing: 0; line-height: 1.25; }.feature-copy > p { color: #69776e; font-size: 14px; line-height: 1.85; }.feature-facts { display: grid; gap: 0; margin: 30px 0 25px; border-top: 1px solid #dfe6e0; }.feature-facts div { display: grid; grid-template-columns: 90px minmax(0, 1fr); gap: 14px; padding: 11px 0; border-bottom: 1px solid #dfe6e0; }.feature-facts dt { color: #8a988e; font-size: 11px; }.feature-facts dd { margin: 0; color: #364039; font-family: var(--sage-font-mono); font-size: 11px; }.inline-link { display: inline-flex; align-items: center; gap: 6px; color: #2f704e; font-size: 12px; font-weight: 600; text-decoration: none; }.inline-link:hover { color: #1a201c; }
.product-preview { min-width: 0; margin: 0; padding: 10px 10px 0; border: 1px solid #cdd9cf; background: #fff; box-shadow: 0 14px 35px rgb(48 80 58 / 8%); }.preview-bar { display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 12px; min-height: 31px; color: #7a887f; font-size: 10px; }.preview-bar > span { display: inline-flex; gap: 4px; }.preview-bar i { width: 6px; height: 6px; border-radius: 50%; background: #c7d3c9; }.preview-bar i:first-child { background: #d88976; }.preview-bar i:nth-child(2) { background: #d6af57; }.preview-bar i:nth-child(3) { background: #76a984; }.preview-bar strong { color: #4f5e54; font-size: 10px; font-weight: 600; }.preview-bar small { color: #95a097; }.product-preview img { display: block; width: 100%; height: auto; border: 1px solid #e1e8e2; object-fit: cover; }.product-preview figcaption { display: flex; justify-content: space-between; gap: 12px; padding: 10px 1px 8px; color: #77857b; font-family: var(--sage-font-mono); font-size: 9px; }
.writing-section { padding-bottom: 94px; }.work-list { border-top: 1px solid #dfe6e0; }.work-entry { border-bottom: 1px solid #dfe6e0; }.work-row { display: grid; grid-template-columns: 54px minmax(0, 1fr) auto; align-items: start; gap: 18px; width: 100%; padding: 23px 0 25px; border: 0; color: #1a201c; background: transparent; text-align: left; cursor: pointer; }.work-row:hover .row-arrow,.work-row:focus-visible .row-arrow { color: #2f704e; }.work-row:focus-visible { outline: none; box-shadow: inset 0 0 0 1px #6f9b7b; }.work-index { padding-top: 3px; color: #9aa69d; font-family: var(--sage-font-mono); font-size: 11px; }.work-main { display: grid; gap: 7px; }.work-main > strong { font-size: 16px; font-weight: 650; }.work-main > small { max-width: 720px; color: #718077; font-size: 12px; line-height: 1.7; }.work-tags { display: flex; flex-wrap: wrap; gap: 7px; }.work-tags em { padding: 4px 7px; border: 1px solid #d3dfd5; color: #688070; background: #f2f7f3; font-size: 10px; font-style: normal; }.row-arrow { margin-top: 5px; color: #a5b0a7; transition: transform .2s ease, color .2s ease; }.row-arrow.expanded { color: #2f704e; transform: rotate(180deg); }.work-evidence { margin: 0 0 8px 72px; padding: 0 0 26px; }.work-evidence dl { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); margin: 0; border-top: 1px solid #dfe6e0; }.work-evidence dl > div { min-width: 0; padding: 17px 28px 17px 0; border-bottom: 1px solid #e6ece7; }.work-evidence dl > div:nth-child(even) { padding-right: 0; padding-left: 28px; border-left: 1px solid #e6ece7; }.work-evidence dt { color: #718077; font-size: 10px; font-weight: 650; }.work-evidence dd { margin: 7px 0 0; color: #445148; font-size: 12px; line-height: 1.7; }.work-evidence footer { display: flex; align-items: center; justify-content: space-between; gap: 18px; padding-top: 15px; }.work-evidence footer a,.work-evidence footer button { display: inline-flex; align-items: center; gap: 6px; padding: 0; border: 0; color: #2f704e; background: transparent; font-size: 11px; font-weight: 650; text-decoration: none; }.work-evidence footer a:hover,.work-evidence footer button:hover { color: #1a201c; }.notes-heading { display: flex; align-items: baseline; gap: 18px; margin-top: 64px; padding-bottom: 17px; border-bottom: 1px solid #dfe6e0; }.notes-heading strong { font-size: 13px; }.notes-list article { display: grid; grid-template-columns: 54px minmax(0, 1fr); gap: 18px; padding: 16px 0; border-bottom: 1px solid #e9eeea; }.notes-list time { color: #9aa69d; font-family: var(--sage-font-mono); font-size: 11px; }.notes-list p { margin: 0; color: #5e6d63; font-size: 13px; }
.path-section { display: grid; grid-template-columns: .94fr 1.06fr; gap: 84px; }.path-section .section-heading { display: block; margin: 0; }.path-section .section-heading p { margin-top: 24px; }.milestone-list { position: relative; display: grid; gap: 0; margin: 0; padding: 0; list-style: none; }.milestone-list::before { position: absolute; top: 17px; bottom: 17px; left: 16px; width: 1px; background: #cfdbd1; content: ''; }.milestone-list li { position: relative; display: grid; grid-template-columns: 33px minmax(0, 1fr); gap: 17px; padding: 0 0 31px; }.milestone-list li:last-child { padding-bottom: 0; }.milestone-mark { position: relative; z-index: 1; display: grid; place-items: center; width: 33px; height: 33px; border: 1px solid #b9c8bc; border-radius: 50%; color: #849287; background: #f8faf8; }.milestone-list li.done .milestone-mark { border-color: #7aae89; color: #2f704e; background: #eef5ef; }.milestone-list li.now .milestone-mark { border-color: #2f704e; color: #fff; background: #2f704e; box-shadow: 0 0 0 5px #e1eee4; }.milestone-list time,.milestone-list strong,.milestone-list p { display: block; }.milestone-list time { color: #8a988e; font-family: var(--sage-font-mono); font-size: 10px; }.milestone-list strong { margin-top: 4px; font-size: 16px; }.milestone-list p { margin: 5px 0 0; color: #718077; font-size: 12px; line-height: 1.7; }
.about-section { display: grid; grid-template-columns: 1.15fr .85fr; gap: 90px; }.about-copy { max-width: 620px; }.about-copy h2 { margin: 14px 0 20px; font-size: 42px; letter-spacing: 0; line-height: 1.16; }.about-copy p { max-width: 560px; margin: 0 0 14px; color: #68766d; font-size: 14px; line-height: 1.85; }.about-aside { display: grid; align-content: start; gap: 0; border-top: 2px solid #2f704e; }.about-aside > div { display: grid; gap: 5px; padding: 17px 0; border-bottom: 1px solid #dfe6e0; }.about-aside span { color: #8a988e; font-size: 10px; letter-spacing: 0; }.about-aside strong { font-size: 15px; }.about-aside small { color: #718077; font-size: 11px; line-height: 1.6; }.about-aside .inline-link { margin-top: 18px; }
.public-footer { display: flex; align-items: center; justify-content: space-between; gap: 20px; width: min(1240px, calc(100% - 64px)); min-height: 104px; margin: 0 auto; }.public-footer > div:first-child { display: grid; gap: 3px; }.public-footer strong { font-size: 13px; }.public-footer span { color: #829087; font-size: 11px; }.footer-links { display: flex; gap: 18px; }.footer-links a,.footer-links button { display: inline-flex; align-items: center; gap: 5px; padding: 0; border: 0; color: #627067; background: transparent; font-size: 11px; text-decoration: none; }.footer-links a:hover,.footer-links button:hover { color: #2f704e; }
.draft-preview { width: min(780px, calc(100% - 36px)); margin: 0 auto; padding: 72px 0 110px; }.back-link { display: inline-flex; align-items: center; gap: 6px; color: #2f704e; font-size: 12px; text-decoration: none; }.back-icon { transform: rotate(180deg); }.draft-preview .eyebrow { margin: 68px 0 7px; }.draft-preview h1 { margin: 0; font-size: 54px; letter-spacing: 0; line-height: 1.1; }.draft-preview > strong { display: block; margin-top: 16px; color: #718077; font-size: 15px; font-weight: 400; line-height: 1.7; }.draft-preview .message-content { margin-top: 40px; padding-top: 28px; border-top: 1px solid #dfe6e0; font-size: 16px; line-height: 1.9; }.draft-preview .message-content :deep(h2) { margin-top: 38px; font-size: 25px; }.draft-preview .message-content :deep(code) { padding: 2px 4px; background: #eef3ef; font-family: var(--sage-font-mono); font-size: 12px; }
.public-agent { position: fixed; z-index: 20; inset: 0 0 0 auto; display: flex; flex-direction: column; width: min(408px, 100%); border-left: 1px solid #cbd8cd; background: #f8faf8; box-shadow: -18px 0 48px rgb(28 55 36 / 14%); }.public-agent > header { display: flex; align-items: center; justify-content: space-between; min-height: 70px; padding: 0 18px; border-bottom: 1px solid #dfe6e0; background: #fff; }.agent-title { display: flex; align-items: center; gap: 10px; }.agent-title > span:last-child { display: grid; gap: 1px; }.agent-title strong { font-size: 13px; }.agent-title small { color: #7c8c81; font-size: 10px; }.agent-mark { border-radius: 50%; }.icon-button { display: grid; place-items: center; width: 30px; height: 30px; padding: 0; border: 0; color: #76847a; background: transparent; }.icon-button:hover { color: #1a201c; background: #edf3ee; }.agent-body { display: grid; align-content: start; gap: 16px; flex: 1; min-height: 0; padding: 20px 18px; overflow: auto; }.agent-message { display: grid; gap: 5px; max-width: 90%; }.agent-message > span { color: #859188; font-family: var(--sage-font-mono); font-size: 10px; }.agent-message p { margin: 0; padding: 11px 12px; color: #445147; background: #fff; font-size: 13px; line-height: 1.7; }.agent-message.visitor { justify-self: end; max-width: 86%; }.agent-message.visitor > span { text-align: right; }.agent-message.visitor p { color: #fff; background: #2f704e; }.agent-sources { display: grid; margin-top: 1px; border-top: 1px solid #dfe6e0; background: #fff; }.agent-sources > strong { padding: 10px 12px 6px; color: #7d8a81; font-size: 9px; font-weight: 650; }.agent-source { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 2px 8px; padding: 8px 12px; border: 0; border-top: 1px solid #edf1ee; color: #2f704e; background: transparent; text-align: left; cursor: pointer; }.agent-source > span { color: #2f704e; font-size: 11px; font-weight: 650; }.agent-source small { grid-column: 1; color: #7c8980; font-size: 9px; line-height: 1.45; }.agent-source svg { grid-row: 1 / span 2; grid-column: 2; align-self: center; }.agent-source:hover { background: #f1f6f2; }.agent-prompts { display: grid; gap: 6px; padding: 16px 18px; border-top: 1px solid #dfe6e0; }.agent-prompts > span { color: #8a988e; font-size: 10px; }.agent-prompts button { padding: 6px 0; border: 0; color: #52715c; background: transparent; text-align: left; font-size: 11px; }.agent-prompts button:hover { color: #1a201c; }.agent-form { display: grid; grid-template-columns: minmax(0, 1fr) 34px; gap: 8px; align-items: end; margin: 0 12px; padding: 10px; border: 1px solid #cbd8cd; background: #fff; }.agent-form textarea { min-height: 58px; resize: none; border: 0; outline: 0; color: #1a201c; background: transparent; font-size: 13px; line-height: 1.55; }.agent-form button { display: grid; place-items: center; width: 34px; height: 34px; border: 0; color: #fff; background: #2f704e; }.agent-form button:hover { background: #255d41; }.agent-form button:disabled { color: #9aa89e; background: #e4ebe5; cursor: not-allowed; }.agent-form textarea:disabled { color: #7f8c83; }.agent-disclaimer { margin: 8px 18px 14px; color: #9aa69d; font-size: 10px; }
@media (max-width: 920px) { .public-header,.public-profile main,.public-footer { width: min(100% - 40px, 720px); }.public-header { grid-template-columns: 1fr auto; }.public-header nav { display: none; }.public-hero { grid-template-columns: 1fr; gap: 36px; min-height: auto; padding: 60px 0 48px; }.hero-system { min-height: 450px; padding: 30px 0 0; border-top: 1px solid #dce6df; border-left: 0; }.feature-grid,.path-section,.about-section { grid-template-columns: 1fr; gap: 45px; }.feature-copy { max-width: 620px; }.path-section .section-heading p { margin-top: 18px; }.section-heading { align-items: start; flex-direction: column; gap: 16px; }.section-heading p { max-width: 400px; }.proof-strip { grid-template-columns: auto 1fr auto; }.proof-strip > span:not(.proof-label) { grid-column: 2; }.proof-strip > a { grid-row: 1 / span 2; grid-column: 3; }.public-section { padding: 72px 0 78px; } }
@media (max-width: 560px) { .public-header,.public-profile main,.public-footer { width: calc(100% - 36px); }.public-header { min-height: 66px; }.public-brand small { display: none; }.ask-sage { min-height: 33px; padding: 0 9px; font-size: 11px; }.hero-copy h1 { font-size: 54px; }.hero-lede { font-size: 24px; }.hero-location { line-height: 1.5; }.hero-signals { gap: 10px 14px; margin-top: 30px; }.hero-system { min-height: 0; padding-bottom: 12px; }.system-heading { display: grid; gap: 8px; }.system-flow { display: grid; grid-template-columns: 1fr; gap: 0; padding: 28px 0 22px 7px; }.flow-stage { grid-template-columns: 43px minmax(0, 1fr); grid-template-rows: auto auto; column-gap: 14px; min-height: 78px; padding: 0; }.flow-stage strong { align-self: end; margin: 0; }.flow-stage small { max-width: none; margin: 3px 0 0; }.flow-node { grid-row: 1 / span 2; }.flow-connector { top: 43px; left: 21px; width: 1px; height: calc(100% - 43px); }.system-foot { grid-template-columns: auto auto; gap: 6px 10px; }.system-foot small { grid-column: 1 / -1; }.proof-strip { grid-template-columns: auto 1fr; gap: 7px 13px; padding: 14px 0; }.proof-strip .proof-label { grid-column: 1 / -1; }.proof-strip > span:not(.proof-label) { grid-column: 1 / -1; }.proof-strip > a { grid-row: 1 / span 3; grid-column: 2; align-self: center; }.public-section { padding: 60px 0 65px; }.section-heading h2 { font-size: 30px; }.feature-copy h3 { font-size: 25px; }.product-preview { padding: 7px 7px 0; }.preview-bar { grid-template-columns: auto 1fr; gap: 7px; }.preview-bar small { display: none; }.work-row { grid-template-columns: 35px minmax(0, 1fr) auto; gap: 10px; }.work-main > strong { font-size: 14px; line-height: 1.4; }.work-main > small { font-size: 11px; }.work-evidence { margin-left: 45px; }.work-evidence dl { grid-template-columns: 1fr; }.work-evidence dl > div,.work-evidence dl > div:nth-child(even) { padding: 14px 0; border-left: 0; }.work-evidence footer { align-items: flex-start; flex-direction: column; gap: 13px; }.notes-heading { display: grid; gap: 8px; }.milestone-list li { gap: 12px; }.about-copy h2 { font-size: 31px; }.public-footer { align-items: flex-start; flex-direction: column; gap: 17px; padding: 27px 0; }.footer-links { gap: 14px; }.draft-preview { padding-top: 48px; }.draft-preview h1 { font-size: 36px; }.public-agent { width: 100%; } }
@media (prefers-reduced-motion: reduce) { :global(html) { scroll-behavior: auto; }.flow-node,.row-arrow { transition: none; } }
</style>
