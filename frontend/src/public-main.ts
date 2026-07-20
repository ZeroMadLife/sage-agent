import { createApp } from 'vue'
import { createPinia } from 'pinia'
import './style.css'
import PublicApp from './PublicApp.vue'
import publicRouter from './router/public'

createApp(PublicApp).use(createPinia()).use(publicRouter).mount('#app')
