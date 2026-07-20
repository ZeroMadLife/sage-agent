<script setup lang="ts">
import { ArrowRight, MessageCircle } from 'lucide-vue-next'
import type { HomeSections } from '../../../public/content'
import workbenchImage from '../../../assets/public/sage-knowledge-workbench.jpg'

defineProps<{
  hero: HomeSections['hero']
  githubUrl: string
}>()

const emit = defineEmits<{
  openAsk: [prompt?: string]
  scrollHarness: []
}>()
</script>

<template>
  <section id="top" class="hero">
    <div class="copy">
      <p class="eyebrow">{{ hero.eyebrow }}</p>
      <h1>{{ hero.title }}</h1>
      <p class="lede">{{ hero.lede }}</p>
      <div class="actions">
        <button class="primary" type="button" @click="emit('scrollHarness')">{{ hero.primaryCta }}</button>
        <a class="secondary" :href="githubUrl" target="_blank" rel="noreferrer">{{ hero.secondaryCta }}</a>
        <button class="text" type="button" @click="emit('openAsk', 'Sage 是做什么的？')">
          <MessageCircle :size="15" />
          {{ hero.askCta }}
        </button>
      </div>
    </div>

    <div class="snapshot" aria-label="系统快照">
      <div class="snapshot-head">
        <span>系统快照</span>
        <strong>Harness 运行时</strong>
      </div>
      <button class="preview" type="button" @click="emit('scrollHarness')">
        <img :src="workbenchImage" alt="Sage 知识工作台预览" />
        <span>
          <strong>真实工作台截面</strong>
          <small>打开体系与证据</small>
        </span>
        <ArrowRight :size="14" />
      </button>
      <div class="snapshot-grid">
        <div v-for="item in hero.snapshot" :key="item.title">
          <strong>{{ item.title }}</strong>
          <small>{{ item.detail }}</small>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.hero {
  display: grid;
  grid-template-columns: minmax(0, 1.05fr) minmax(300px, .95fr);
  gap: clamp(28px, 5vw, 56px);
  align-items: center;
  padding: 42px 0 28px;
}
.eyebrow {
  margin: 0 0 14px;
  color: var(--pub-muted);
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.copy h1 {
  margin: 0;
  font-size: clamp(34px, 5vw, 54px);
  line-height: 1.08;
  letter-spacing: -0.03em;
  white-space: pre-line;
}
.lede {
  max-width: 52ch;
  margin: 16px 0 0;
  color: var(--pub-muted);
  font-size: 15px;
  line-height: 1.75;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  margin-top: 22px;
}
.primary,
.secondary,
.text {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 40px;
  border-radius: 12px;
  font-size: 13px;
  text-decoration: none;
}
.primary {
  padding: 0 14px;
  border: 1px solid var(--pub-brand);
  color: #fff;
  background: var(--pub-brand);
}
.secondary {
  padding: 0 14px;
  border: 1px solid var(--pub-border);
  color: var(--pub-text);
  background: var(--pub-surface);
}
.text {
  padding: 0;
  border: 0;
  color: var(--pub-brand-strong);
  background: transparent;
}
.snapshot {
  display: grid;
  gap: 12px;
  padding: 16px;
  border: 1px solid var(--pub-border);
  border-radius: 18px;
  background: color-mix(in srgb, var(--pub-surface) 92%, transparent);
  box-shadow: 0 16px 40px rgb(24 40 28 / 8%);
}
.snapshot-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  color: var(--pub-muted);
  font-size: 11px;
}
.snapshot-head strong { color: var(--pub-text); }
.preview {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 8px 10px;
  padding: 0;
  overflow: hidden;
  border: 1px solid var(--pub-border);
  border-radius: 14px;
  color: var(--pub-text);
  background: var(--pub-bg);
  text-align: left;
}
.preview img {
  grid-column: 1 / -1;
  display: block;
  width: 100%;
  height: 120px;
  object-fit: cover;
}
.preview > span {
  display: grid;
  gap: 2px;
  padding: 0 0 10px 12px;
}
.preview strong { font-size: 12px; }
.preview small { color: var(--pub-muted); font-size: 10px; }
.preview > svg {
  margin: 0 12px 12px 0;
  color: var(--pub-brand);
}
.snapshot-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}
.snapshot-grid div {
  display: grid;
  gap: 3px;
  padding: 12px;
  border-radius: 12px;
  background: color-mix(in srgb, var(--pub-brand) 8%, var(--pub-bg));
}
.snapshot-grid strong { font-size: 12px; }
.snapshot-grid small { color: var(--pub-muted); font-size: 11px; }
@media (max-width: 900px) {
  .hero { grid-template-columns: 1fr; }
}
</style>
