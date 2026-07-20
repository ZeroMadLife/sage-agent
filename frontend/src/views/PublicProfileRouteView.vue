<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import PublicDraftPreview from '../components/public/PublicDraftPreview.vue'
import PublicProfileView from './PublicProfileView.vue'

const route = useRoute()
const previewingDraft = computed(() => route.query.preview === 'draft')
</script>

<template>
  <PublicDraftPreview v-if="previewingDraft" />
  <PublicProfileView v-else />
</template>
