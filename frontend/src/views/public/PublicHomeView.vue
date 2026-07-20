<script setup lang="ts">
import PublicAppShell from '../../components/public/PublicAppShell.vue'
import HeroProblem from '../../components/public/home/HeroProblem.vue'
import HarnessSystem from '../../components/public/home/HarnessSystem.vue'
import EvidenceGallery from '../../components/public/home/EvidenceGallery.vue'
import GithubProof from '../../components/public/home/GithubProof.vue'
import NotesPreview from '../../components/public/home/NotesPreview.vue'
import GrowthPath from '../../components/public/home/GrowthPath.vue'
import { getHomeSections, getSiteMeta, listNotes } from '../../public/content'
import { resolveGithubProof } from '../../public/githubMeta'

const site = getSiteMeta()
const sections = getHomeSections()
const notes = listNotes().slice(0, 3)
const github = resolveGithubProof()

function scrollHarness() {
  document.querySelector('#harness')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
</script>

<template>
  <PublicAppShell
    :brand="site.brand"
    :github-url="github.htmlUrl"
    :star-label="github.starLabel"
    :show-stars="github.showStars"
  >
    <template #default="{ openAsk }">
      <HeroProblem
        :hero="sections.hero"
        :github-url="github.htmlUrl"
        @open-ask="openAsk"
        @scroll-harness="scrollHarness"
      />
      <HarnessSystem :harness="sections.harness" />
      <EvidenceGallery :evidence="sections.evidence" />
      <div class="stack">
        <GithubProof
          :html-url="github.htmlUrl"
          :show-stars="github.showStars"
          :star-label="github.starLabel"
        />
        <NotesPreview :notes="notes" />
      </div>
      <GrowthPath :path="sections.path" />
      <section id="about" class="about">
        <div>
          <span class="eyebrow">{{ sections.about.eyebrow }}</span>
          <h2>{{ sections.about.title }}</h2>
          <p v-for="paragraph in sections.about.paragraphs" :key="paragraph">{{ paragraph }}</p>
        </div>
        <div class="aside">
          <div>
            <span>开源</span>
            <strong>可阅读的实现</strong>
            <small>代码、设计文档和验收路径都在仓库里。</small>
          </div>
          <div>
            <span>公开面</span>
            <strong>有限但诚实</strong>
            <small>公开问答只覆盖已经发布的资料，不伪装成完整 Agent。</small>
          </div>
        </div>
      </section>
    </template>
  </PublicAppShell>
</template>

<style scoped>
.stack {
  display: grid;
  gap: 18px;
  padding: 18px 0 8px;
}
.about {
  display: grid;
  grid-template-columns: 1.15fr .85fr;
  gap: 36px;
  padding: 42px 0 28px;
}
.eyebrow {
  color: var(--pub-muted);
  font-size: 11px;
  letter-spacing: 0.08em;
}
.about h2 {
  margin: 12px 0 16px;
  font-size: clamp(26px, 3vw, 38px);
  letter-spacing: -0.03em;
  line-height: 1.15;
}
.about p {
  max-width: 58ch;
  margin: 0 0 12px;
  color: var(--pub-muted);
  font-size: 14px;
  line-height: 1.8;
}
.aside {
  display: grid;
  align-content: start;
  border-top: 2px solid var(--pub-brand);
}
.aside > div {
  display: grid;
  gap: 5px;
  padding: 16px 0;
  border-bottom: 1px solid var(--pub-border);
}
.aside span {
  color: var(--pub-muted);
  font-size: 10px;
}
.aside strong { font-size: 15px; }
.aside small {
  color: var(--pub-muted);
  font-size: 12px;
  line-height: 1.6;
}
@media (max-width: 800px) {
  .about { grid-template-columns: 1fr; }
}
</style>
