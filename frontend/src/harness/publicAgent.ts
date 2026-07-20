export type PublicAgentSource = {
  id: 'sage' | 'harness' | 'knowledge' | 'growth'
  label: string
  target: 'work' | 'writing' | 'path'
  detail: string
}

export type PublicAgentResponse = {
  mode: 'static'
  answer: string
  sources: PublicAgentSource[]
}

type PublicAnswerEntry = {
  keywords: string[]
  answer: string
  sources: PublicAgentSource[]
}

const sources = {
  sage: {
    id: 'sage',
    label: 'Sage 项目现场',
    target: 'work',
    detail: '目标、Knowledge、Practice 与 Evidence 的产品闭环',
  },
  harness: {
    id: 'harness',
    label: 'Chat Harness 2.0',
    target: 'writing',
    detail: '统一 timeline、审批与恢复语义的工程案例',
  },
  knowledge: {
    id: 'knowledge',
    label: 'Knowledge Surface',
    target: 'writing',
    detail: '图谱、RAG、revision 与冻结上下文的设计取舍',
  },
  growth: {
    id: 'growth',
    label: 'Learning Path',
    target: 'path',
    detail: '按真实证据记录的公开成长轨迹',
  },
} satisfies Record<string, PublicAgentSource>

const entries: PublicAnswerEntry[] = [
  {
    keywords: ['sage', '做什么', '项目', '学习助手'],
    answer: 'Sage 是一个 Personal AI Learning Companion：用户先设定目标，再让主对话结合个人知识和外部证据，安排练习并记录可验证进步。Knowledge 用来查看和治理知识结构，Coding 则是按需调用的 Practice Engine。',
    sources: [sources.sage],
  },
  {
    keywords: ['harness', '恢复', '运行', 'timeline', '审批'],
    answer: 'Harness 2.0 把 planning、tool、approval、reply 和 terminal 统一投影到 durable Timeline。刷新或断线后，界面从同一条审计事实恢复，而不是重新伪造模型状态。',
    sources: [sources.harness],
  },
  {
    keywords: ['知识', '图谱', 'rag', 'wiki', '节点'],
    answer: 'Knowledge 页面主要呈现个人知识库的结构和来源。真正的学习动作发生在主对话；选中的 graph node、page 和 revision 只在提交下一轮问题时进入冻结上下文。',
    sources: [sources.knowledge, sources.sage],
  },
  {
    keywords: ['成长', '进度', '掌握', '证据', '面试'],
    answer: '公开成长记录不展示模型自评百分比，而是展示已经形成的项目、时间线、引用、测试或实践结果。没有可验证证据时，Sage 会保持“尚未验证”。',
    sources: [sources.growth],
  },
]

export async function answerPublicProfileQuestion(question: string): Promise<PublicAgentResponse> {
  const normalized = question.trim().toLocaleLowerCase()
  const match = entries
    .map((entry) => ({
      entry,
      score: entry.keywords.reduce(
        (total, keyword) => total + (normalized.includes(keyword.toLocaleLowerCase()) ? 1 : 0),
        0,
      ),
    }))
    .filter((candidate) => candidate.score > 0)
    .sort((left, right) => right.score - left.score)[0]?.entry

  if (!match) {
    return {
      mode: 'static',
      answer: '这版问答只覆盖已经公开的 Sage、Harness、Knowledge 和成长记录。私有工作区、Session、Memory 与未发布资料不会进入这个公开入口。',
      sources: [],
    }
  }

  return {
    mode: 'static',
    answer: match.answer,
    sources: match.sources,
  }
}
