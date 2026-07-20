<script setup lang="ts">
import { ArrowRight } from 'lucide-vue-next'
import type { PublicNote } from '../../../public/content'

defineProps<{
  notes: PublicNote[]
}>()
</script>

<template>
  <section id="notes" class="notes-preview">
    <div class="heading">
      <div>
        <span class="eyebrow">笔记</span>
        <h2>工程笔记</h2>
      </div>
      <RouterLink to="/notes">查看全部 <ArrowRight :size="14" /></RouterLink>
    </div>
    <div class="list">
      <RouterLink
        v-for="note in notes"
        :key="note.slug"
        class="note-card"
        :to="`/notes/${note.slug}`"
      >
        <time>{{ note.date }}</time>
        <strong>{{ note.title }}</strong>
        <p>{{ note.summary }}</p>
        <div class="tags">
          <em v-for="tag in note.tags" :key="tag">{{ tag }}</em>
        </div>
      </RouterLink>
    </div>
  </section>
</template>

<style scoped>
.notes-preview { padding: 34px 0 10px; }
.heading {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}
.eyebrow {
  display: block;
  margin-bottom: 8px;
  color: var(--pub-muted);
  font-size: 11px;
  letter-spacing: 0.08em;
}
h2 {
  margin: 0;
  font-size: clamp(24px, 3vw, 34px);
  letter-spacing: -0.03em;
}
.heading a {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--pub-brand-strong);
  text-decoration: none;
  font-size: 13px;
}
.list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.note-card {
  display: grid;
  gap: 8px;
  padding: 16px;
  border: 1px solid var(--pub-border);
  border-radius: 16px;
  color: inherit;
  background: var(--pub-surface);
  text-decoration: none;
}
.note-card:hover {
  border-color: color-mix(in srgb, var(--pub-brand) 35%, var(--pub-border));
  transform: translateY(-1px);
}
time {
  color: var(--pub-muted);
  font-family: var(--sage-font-mono, ui-monospace, monospace);
  font-size: 11px;
}
strong { font-size: 16px; letter-spacing: -0.02em; }
p {
  margin: 0;
  color: var(--pub-muted);
  font-size: 13px;
  line-height: 1.65;
}
.tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.tags em {
  padding: 4px 8px;
  border: 1px solid var(--pub-border);
  border-radius: 999px;
  color: var(--pub-brand-strong);
  background: color-mix(in srgb, var(--pub-brand) 8%, var(--pub-bg));
  font-size: 10px;
  font-style: normal;
}
@media (max-width: 720px) {
  .list { grid-template-columns: 1fr; }
}
@media (prefers-reduced-motion: reduce) {
  .note-card:hover { transform: none; }
}
</style>
