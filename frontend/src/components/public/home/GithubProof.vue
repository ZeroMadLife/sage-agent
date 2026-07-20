<script setup lang="ts">
import { ArrowUpRight, Github } from 'lucide-vue-next'

defineProps<{
  htmlUrl: string
  showStars: boolean
  starLabel: string | null
}>()
</script>

<template>
  <section class="github-proof" aria-label="GitHub 证明">
    <div>
      <span class="eyebrow">GitHub</span>
      <h3>ZeroMadLife/sage-agent</h3>
      <p>源码、设计文档与测试都在仓库里，可按路径追溯 Harness、知识面与实践引擎。</p>
      <p v-if="showStars && starLabel" class="stars">★ {{ starLabel }} stars</p>
      <p v-else class="stars muted">Star 数在构建时注入；当前为降级展示</p>
    </div>
    <a :href="htmlUrl" target="_blank" rel="noreferrer">
      <Github :size="16" />
      打开仓库
      <ArrowUpRight :size="14" />
    </a>
  </section>
</template>

<style scoped>
.github-proof {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 18px;
  padding: 22px;
  border: 1px solid var(--pub-border);
  border-radius: 18px;
  background:
    linear-gradient(135deg, color-mix(in srgb, var(--pub-brand) 12%, var(--pub-surface)), var(--pub-surface));
}
.eyebrow {
  color: var(--pub-muted);
  font-size: 11px;
  letter-spacing: 0.08em;
}
h3 {
  margin: 8px 0 8px;
  font-size: 22px;
  letter-spacing: -0.03em;
}
p {
  max-width: 52ch;
  margin: 0;
  color: var(--pub-muted);
  font-size: 13px;
  line-height: 1.7;
}
.stars {
  margin-top: 10px;
  color: var(--pub-text);
  font-family: var(--sage-font-mono, ui-monospace, monospace);
  font-size: 12px;
}
.stars.muted { color: var(--pub-muted); }
a {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 40px;
  padding: 0 14px;
  border-radius: 12px;
  color: #fff;
  background: var(--pub-brand);
  text-decoration: none;
  font-size: 13px;
  white-space: nowrap;
}
@media (max-width: 720px) {
  .github-proof {
    align-items: stretch;
    flex-direction: column;
  }
}
</style>
