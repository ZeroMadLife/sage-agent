<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Github, KeyRound, LogIn, LoaderCircle } from 'lucide-vue-next'
import { useRoute } from 'vue-router'
import { useCloudAuth } from '../../composables/useCloudAuth'

defineProps<{ checking: boolean }>()
const emit = defineEmits<{ authenticated: [] }>()
const route = useRoute()
const { loginWithInvite, options, startGitHubLogin } = useCloudAuth()
const inviteCode = ref('')
const error = ref('')
const submitting = ref(false)
const githubSubmitting = ref(false)
const methodsChecking = ref(true)
const canaryInviteLogin = ref(false)
const githubLogin = ref(true)
const returnTo = computed(() => `/#${route.fullPath}`)
const busy = computed(() => submitting.value || githubSubmitting.value)

onMounted(async () => {
  try {
    const enabled = await options()
    canaryInviteLogin.value = enabled.canary_invite_login
    githubLogin.value = enabled.github_login
  } finally {
    methodsChecking.value = false
  }
})

function deviceName(): string {
  const userAgent = navigator.userAgent
  if (/iPhone/i.test(userAgent)) return 'iPhone Safari'
  if (/iPad/i.test(userAgent)) return 'iPad Safari'
  if (/Android/i.test(userAgent)) return 'Android 浏览器'
  const platform = navigator.platform?.trim()
  return platform ? `${platform} 浏览器` : '浏览器设备'
}

async function submit() {
  if (!inviteCode.value.trim() || busy.value) return
  submitting.value = true
  error.value = ''
  try {
    await loginWithInvite(inviteCode.value.trim(), deviceName())
    emit('authenticated')
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '登录失败，请稍后重试'
  } finally {
    submitting.value = false
  }
}

async function submitGitHub() {
  if (!inviteCode.value.trim() || busy.value) return
  githubSubmitting.value = true
  error.value = ''
  try {
    const authorizationUrl = await startGitHubLogin(inviteCode.value.trim(), returnTo.value)
    window.location.assign(authorizationUrl)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'GitHub 登录失败，请稍后重试'
    githubSubmitting.value = false
  }
}
</script>

<template>
  <main class="cloud-auth-gate">
    <section class="cloud-auth-panel" aria-labelledby="cloud-auth-title">
      <div class="cloud-auth-mark" aria-hidden="true">
        <KeyRound :size="20" />
      </div>
      <p class="cloud-auth-eyebrow">SAGE PRIVATE CANARY</p>
      <h1 id="cloud-auth-title">登录 Sage</h1>
      <p class="cloud-auth-copy">这是邀请制私有环境。首次输入邀请码，之后此设备会保持登录。</p>

      <div v-if="checking || methodsChecking" class="cloud-auth-checking" role="status">
        <LoaderCircle class="cloud-auth-spinner" :size="18" />
        <span>{{ checking ? '正在检查登录状态' : '正在准备登录方式' }}</span>
      </div>
      <form v-else class="cloud-auth-form" @submit.prevent="canaryInviteLogin ? submit() : submitGitHub()">
        <label for="cloud-invite-code">邀请码</label>
        <input
          id="cloud-invite-code"
          v-model="inviteCode"
          autocomplete="one-time-code"
          placeholder="输入一次性邀请码"
          :disabled="busy"
        />
        <p v-if="error" class="cloud-auth-error" role="alert">{{ error }}</p>
        <button v-if="canaryInviteLogin" type="submit" :disabled="!inviteCode.trim() || busy">
          <LoaderCircle v-if="submitting" class="cloud-auth-spinner" :size="17" />
          <LogIn v-else :size="17" />
          <span>{{ submitting ? '正在登录' : '使用邀请码进入' }}</span>
        </button>
        <button
          v-if="githubLogin"
          type="button"
          class="cloud-auth-secondary"
          :disabled="!inviteCode.trim() || busy"
          @click="submitGitHub"
        >
          <LoaderCircle v-if="githubSubmitting" class="cloud-auth-spinner" :size="17" />
          <Github v-else :size="17" />
          <span>{{ githubSubmitting ? '正在跳转' : '改用 GitHub 登录' }}</span>
        </button>
      </form>
    </section>
  </main>
</template>

<style scoped>
.cloud-auth-gate {
  min-height: 100dvh;
  display: grid;
  place-items: center;
  padding: 24px;
  color: var(--sage-text);
  background: var(--sage-bg);
}

.cloud-auth-panel {
  width: min(100%, 420px);
  padding: 32px;
  border: 1px solid var(--sage-border);
  border-radius: 8px;
  background: var(--sage-surface);
  box-shadow: var(--sage-shadow-drawer);
}

.cloud-auth-mark {
  width: 40px;
  height: 40px;
  display: grid;
  place-items: center;
  color: var(--sage-source);
  background: var(--sage-source-bg);
  border-radius: 8px;
}

.cloud-auth-eyebrow {
  margin: 24px 0 8px;
  color: var(--sage-source);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.12em;
}

h1 {
  margin: 0;
  color: var(--sage-text);
  font-size: 28px;
}

.cloud-auth-copy {
  margin: 10px 0 24px;
  color: var(--sage-text-secondary);
  line-height: 1.6;
}

.cloud-auth-form {
  display: grid;
  gap: 10px;
}

label {
  color: var(--sage-text-secondary);
  font-size: 13px;
  font-weight: 600;
}

input {
  box-sizing: border-box;
  width: 100%;
  min-height: 44px;
  padding: 0 12px;
  border: 1px solid var(--sage-border-strong);
  border-radius: 6px;
  color: var(--sage-text);
  background: var(--sage-surface-raised);
  font: inherit;
}

input:focus {
  border-color: var(--sage-source);
}

button {
  min-height: 44px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin-top: 8px;
  border: 0;
  border-radius: 6px;
  color: #fff;
  background: var(--sage-source);
  font: inherit;
  font-weight: 650;
  cursor: pointer;
}

button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.cloud-auth-secondary {
  margin-top: 0;
  border: 1px solid var(--sage-border-strong);
  color: var(--sage-text-secondary);
  background: var(--sage-surface-raised);
}

.cloud-auth-secondary:hover:not(:disabled) {
  color: var(--sage-text);
  background: var(--sage-surface-muted);
}

.cloud-auth-error {
  margin: 2px 0 0;
  color: var(--sage-danger);
  font-size: 13px;
}

.cloud-auth-checking {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--sage-text-secondary);
}

.cloud-auth-spinner {
  animation: cloud-auth-spin 0.9s linear infinite;
}

@keyframes cloud-auth-spin {
  to { transform: rotate(360deg); }
}

@media (max-width: 480px) {
  .cloud-auth-gate { padding: 16px; }
  .cloud-auth-panel { padding: 24px; }
}

@media (max-height: 560px) {
  .cloud-auth-gate { place-items: start center; overflow: auto; }
  .cloud-auth-panel { margin: 16px 0; }
  .cloud-auth-eyebrow { margin-top: 16px; }
  .cloud-auth-copy { margin-bottom: 16px; }
}
</style>
