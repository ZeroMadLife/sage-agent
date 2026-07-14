import { createRouter, createWebHashHistory } from 'vue-router'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    {
      path: '/',
      redirect: '/coding',
    },
    {
      path: '/coding',
      name: 'coding.home',
      component: () => import('../views/CodingView.vue'),
    },
    {
      path: '/coding/session/:sessionId',
      name: 'coding.session',
      component: () => import('../views/CodingView.vue'),
    },
    {
      path: '/settings/:section?',
      name: 'settings',
      component: () => import('../views/SettingsView.vue'),
    },
    {
      path: '/:pathMatch(.*)*',
      redirect: '/coding',
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
