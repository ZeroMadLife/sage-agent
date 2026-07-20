<script setup lang="ts">
import { computed } from 'vue'
import { ArrowRight } from 'lucide-vue-next'
import { useMarkdown } from '../../composables/useMarkdown'
import { loadPublishingDraft } from '../../harness/publishingDraft'

const draft = loadPublishingDraft()
const { render } = useMarkdown()
const draftBody = computed(() => render(draft.body))
</script>

<template>
  <article class="draft-preview">
    <RouterLink class="back-link" to="/public"><ArrowRight :size="15" class="back-icon" />返回公开主页</RouterLink>
    <p class="eyebrow">PUBLIC DRAFT</p>
    <h1>{{ draft.title }}</h1>
    <strong>{{ draft.summary }}</strong>
    <div class="message-content" v-html="draftBody"></div>
  </article>
</template>

<style scoped>
.draft-preview { width: min(780px, calc(100% - 36px)); margin: 0 auto; padding: 72px 0 110px; }
.back-link { display: inline-flex; align-items: center; gap: 6px; color: #2f704e; font-size: 12px; text-decoration: none; }
.back-icon { transform: rotate(180deg); }
.eyebrow { margin: 68px 0 7px; color: #7c8c81; font-size: 10px; letter-spacing: 0; text-transform: uppercase; }
h1 { margin: 0; font-size: 54px; letter-spacing: 0; line-height: 1.1; }
article > strong { display: block; margin-top: 16px; color: #718077; font-size: 15px; font-weight: 400; line-height: 1.7; }
.message-content { margin-top: 40px; padding-top: 28px; border-top: 1px solid #dfe6e0; font-size: 16px; line-height: 1.9; }
.message-content :deep(h2) { margin-top: 38px; font-size: 25px; }
.message-content :deep(code) { padding: 2px 4px; background: #eef3ef; font-family: var(--sage-font-mono); font-size: 12px; }
@media (max-width: 560px) { .draft-preview { padding-top: 48px; } h1 { font-size: 36px; } }
</style>
