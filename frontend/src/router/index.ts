import { createRouter, createWebHashHistory } from 'vue-router'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    {
      path: '/',
      redirect: '/assistant',
    },
    {
      path: '/assistant',
      name: 'assistant.home',
      meta: { assistantShell: true },
      component: () => import('../views/AssistantHomeView.vue'),
    },
    {
      path: '/coding',
      name: 'coding.home',
      meta: { assistantShell: true },
      component: () => import('../views/CodingView.vue'),
    },
    {
      path: '/coding/session/:sessionId',
      name: 'coding.session',
      meta: { assistantShell: true },
      component: () => import('../views/CodingView.vue'),
    },
    {
      path: '/settings/:section?',
      name: 'settings',
      component: () => import('../views/SettingsView.vue'),
    },
    {
      path: '/knowledge',
      name: 'knowledge',
      meta: { assistantShell: true },
      component: () => import('../views/KnowledgeView.vue'),
    },
    {
      path: '/evolution',
      name: 'evolution',
      meta: { assistantShell: true },
      component: () => import('../views/EvolutionView.vue'),
    },
    {
      path: '/public',
      name: 'public.profile',
      meta: { assistantShell: true },
      component: () => import('../views/PublicProfileView.vue'),
    },
    {
      path: '/:pathMatch(.*)*',
      redirect: '/assistant',
    },
  ],
})

const settingsSections = new Set([
  'appearance', 'providers', 'models', 'usage', 'skills', 'mcp', 'memory', 'context', 'sessions', 'workspace', 'runs',
])

router.beforeEach((to) => {
  if (to.name !== 'settings') return true
  return typeof to.params.section === 'string' && settingsSections.has(to.params.section)
    ? true
    : { name: 'settings', params: { section: 'appearance' }, replace: true }
})

export default router
