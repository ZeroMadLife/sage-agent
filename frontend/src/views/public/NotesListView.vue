<script setup lang="ts">
import PublicAppShell from '../../components/public/PublicAppShell.vue'
import NoteCard from '../../components/public/notes/NoteCard.vue'
import { getSiteMeta, listNotes } from '../../public/content'
import { resolveGithubProof } from '../../public/githubMeta'

const site = getSiteMeta()
const notes = listNotes()
const github = resolveGithubProof()
</script>

<template>
  <PublicAppShell
    :brand="site.brand"
    :github-url="github.htmlUrl"
    :star-label="github.starLabel"
    :show-stars="github.showStars"
  >
    <section class="notes-page">
      <div class="heading">
        <span class="eyebrow">工程笔记</span>
        <h1>只读的公开笔记</h1>
        <p>这里只展示已经发布的工程判断。写作入口在私有发布工作室，不会出现在公开站上。</p>
      </div>
      <div class="list">
        <NoteCard v-for="note in notes" :key="note.slug" :note="note" />
      </div>
      <p class="author-note">作者写作请使用私有应用中的发布工作室；公开站保持只读，避免把编辑能力暴露给所有访客。</p>
    </section>
  </PublicAppShell>
</template>

<style scoped>
.notes-page { padding: 36px 0 48px; }
.heading {
  display: grid;
  gap: 10px;
  margin-bottom: 22px;
}
.eyebrow {
  color: var(--pub-muted);
  font-size: 11px;
  letter-spacing: 0.08em;
}
h1 {
  margin: 0;
  font-size: clamp(30px, 4vw, 42px);
  letter-spacing: -0.03em;
}
p {
  margin: 0;
  color: var(--pub-muted);
  font-size: 14px;
  line-height: 1.7;
}
.list {
  display: grid;
  gap: 12px;
}
.author-note {
  margin: 22px 0 0;
  padding: 14px 16px;
  border: 1px dashed var(--pub-border);
  border-radius: 14px;
  color: var(--pub-muted);
  background: color-mix(in srgb, var(--pub-brand) 6%, var(--pub-surface));
  font-size: 13px;
  line-height: 1.7;
}
</style>
