import { computed, ref, shallowRef } from 'vue'
import type {
  LearningSourcePolicy,
  LearningTaskPatchInput,
  LearningTaskResponse,
} from '../types/api'

type LearningStartingLevel = NonNullable<
  LearningTaskResponse['learner_profile']['starting_level']
>
type LearningDraftPatch = Omit<LearningTaskPatchInput, 'expected_revision' | 'source_policy'> & {
  source_policy: LearningSourcePolicy
}

export function useLearningConfirmationForm() {
  const canonicalTask = shallowRef<LearningTaskResponse | null>(null)
  const topic = ref('')
  const desiredOutcome = ref('')
  const startingLevel = ref<'' | LearningStartingLevel>('')
  const timeBudget = ref<number | null>(null)
  const targetDate = ref('')
  const knowledgePolicy = ref<LearningSourcePolicy['knowledge']>('preferred')
  const webPolicy = ref<LearningSourcePolicy['web']>('allowed_when_insufficient')
  const freshness = ref<LearningSourcePolicy['freshness']>('all')
  const domainsText = ref('')

  function normalizedDomains(): string[] {
    return [...new Set(
      domainsText.value
        .split(',')
        .map((domain) => domain.trim().toLowerCase())
        .filter(Boolean),
    )]
  }

  function sync(task: LearningTaskResponse | null): void {
    canonicalTask.value = task
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
  }

  function toPatch(): LearningDraftPatch {
    return {
      topic: topic.value.trim(),
      desired_outcome: desiredOutcome.value.trim() || null,
      starting_level: startingLevel.value || null,
      time_budget_minutes_per_week: timeBudget.value,
      target_date: targetDate.value || null,
      source_policy: {
        knowledge: knowledgePolicy.value,
        web: webPolicy.value,
        domains: normalizedDomains(),
        freshness: freshness.value,
      },
    }
  }

  const dirty = computed(() => {
    const task = canonicalTask.value
    if (!task) return false
    const patch = toPatch()
    return patch.topic !== task.topic
      || patch.desired_outcome !== task.desired_outcome
      || patch.starting_level !== task.learner_profile.starting_level
      || patch.time_budget_minutes_per_week
        !== task.learner_profile.time_budget_minutes_per_week
      || patch.target_date !== task.learner_profile.target_date
      || patch.source_policy.knowledge !== task.source_policy.knowledge
      || patch.source_policy.web !== task.source_policy.web
      || patch.source_policy.freshness !== task.source_policy.freshness
      || patch.source_policy.domains.join(',') !== task.source_policy.domains.join(',')
  })

  const localReady = computed(() => Boolean(
    topic.value.trim()
      && desiredOutcome.value.trim()
      && startingLevel.value
      && timeBudget.value !== null
      && timeBudget.value >= 15,
  ))

  const sourcePolicyLabel = computed(() => {
    const knowledge = knowledgePolicy.value === 'required'
      ? '必须使用本地知识'
      : knowledgePolicy.value === 'disabled' ? '不使用本地知识' : '优先使用本地知识'
    const web = webPolicy.value === 'forbidden' ? '不联网' : '证据不足时允许联网'
    return `${knowledge}，${web}${freshness.value === 'current' ? '，仅使用当前资料' : ''}`
  })

  return {
    desiredOutcome,
    dirty,
    domainsText,
    freshness,
    knowledgePolicy,
    localReady,
    normalizedDomains,
    sourcePolicyLabel,
    startingLevel,
    sync,
    targetDate,
    timeBudget,
    toPatch,
    topic,
    webPolicy,
  }
}
