import { createRouter, createWebHistory } from 'vue-router'
import PublicProfileView from '../views/PublicProfileView.vue'

const publicRouter = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'public.home',
      component: PublicProfileView,
    },
    {
      path: '/public',
      redirect: '/',
    },
    {
      path: '/:pathMatch(.*)*',
      redirect: '/',
    },
  ],
})

export default publicRouter
