<script setup lang="ts">
const props = defineProps<{
  tool: string
  status: 'running' | 'done' | 'error'
  message?: string
  args?: Record<string, unknown>
}>()

const toolLabels: Record<string, string> = {
  search_nearby: '周边搜索',
  get_poi_detail: '地点详情',
  search_attractions: '景点搜索',
  get_weather: '实时天气',
  get_forecast: '天气预报',
  get_route: '路线规划',
  geocode: '地址定位',
  search_scenic_spots: '本地景点库',
  get_scenic_detail: '景点详情',
  generate_itinerary: '多Agent行程规划',
}

const argLabels: Record<string, string> = {
  city: '城市',
  days: '天数',
  destination: '目的地',
  budget_total: '预算',
  preferences: '偏好',
  dates: '日期',
  keywords: '关键词',
  category: '分类',
  location: '位置',
  limit: '数量',
  radius: '半径',
  poi_id: '地点ID',
  origin: '起点',
  destination_location: '终点',
  mode: '方式',
}

const statusLabels: Record<string, string> = {
  running: '执行中',
  done: '完成',
  error: '失败',
}

function toolLabel() {
  return toolLabels[props.tool] ?? props.tool
}

function summaryMessage() {
  return props.message || `${toolLabel()}${statusLabels[props.status]}`
}

function formatArg(key: string, value: unknown) {
  const label = argLabels[key] ?? key
  if (Array.isArray(value)) {
    return `${label}：${value.join('、')}`
  }
  if (typeof value === 'object' && value !== null) {
    const maybeDateRange = value as { start?: unknown; end?: unknown }
    if (maybeDateRange.start || maybeDateRange.end) {
      return `${label}：${String(maybeDateRange.start ?? '')}至${String(maybeDateRange.end ?? '')}`
    }
    return `${label}：已提供`
  }
  return `${label}：${String(value)}`
}

function readableArgs() {
  return Object.entries(props.args ?? {})
    .filter(([, value]) => value !== undefined && value !== null && String(value) !== '')
    .map(([key, value]) => formatArg(key, value))
}
</script>

<template>
  <div class="tool-call">
    <div class="tool-heading">
      <span class="status-dot" :class="status" />
      <span class="name">{{ summaryMessage() }}</span>
      <span class="status" :class="status">{{ statusLabels[status] }}</span>
    </div>
    <div class="tool-detail">
      <span>工具：{{ toolLabel() }}</span>
      <span v-for="item in readableArgs()" :key="item">{{ item }}</span>
    </div>
  </div>
</template>

<style scoped>
.tool-call {
  padding: 0.45rem 0.35rem;
  font-size: 0.85rem;
  color: #4b5563;
  border-top: 1px solid #e5e7eb;
}

.tool-call:first-child {
  border-top: 0;
}

.tool-heading {
  display: flex;
  align-items: center;
  gap: 0.45rem;
}

.status-dot {
  width: 0.42rem;
  height: 0.42rem;
  border-radius: 999px;
  background: #9ca3af;
  flex-shrink: 0;
}

.status-dot.running { background: #f59e0b; }
.status-dot.done { background: #10b981; }
.status-dot.error { background: #ef4444; }

.name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tool-detail {
  display: flex;
  flex-wrap: wrap;
  gap: 0.2rem 0.65rem;
  margin-top: 0.25rem;
  padding-left: 0.9rem;
  color: #6b7280;
  line-height: 1.45;
}

.status.running { color: #f59e0b; }
.status.done { color: #10b981; }
.status.error { color: #ef4444; }
</style>
