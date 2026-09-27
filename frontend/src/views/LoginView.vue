<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { login } from '../api'

const router = useRouter()
const username = ref('admin')
const password = ref('')
const loading = ref(false)
const err = ref('')

async function submit() {
  if (!username.value || !password.value) { err.value = '请输入用户名和密码'; return }
  loading.value = true
  err.value = ''
  try {
    await login(username.value, password.value)
    router.push('/')
  } catch (e: any) {
    err.value = e?.response?.data?.detail ?? '登录失败，请重试'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login">
    <div class="card">
      <div class="mark">星图</div>
      <h1>商单助手</h1>
      <p class="sub">对话式 MCN 业务工作台</p>
      <form @submit.prevent="submit" class="form">
        <label class="field">
          <span>用户名</span>
          <input v-model="username" autocomplete="username" />
        </label>
        <label class="field">
          <span>密码</span>
          <input v-model="password" type="password" autocomplete="current-password" />
        </label>
        <div v-if="err" class="err">{{ err }}</div>
        <button class="btn" :disabled="loading" type="submit">
          {{ loading ? '登录中…' : '登录' }}
        </button>
      </form>
    </div>
  </div>
</template>

<style scoped>
.login { height: 100vh; display: flex; align-items: center; justify-content: center;
  background: var(--paper); }
.card { width: 360px; background: var(--surface); border: 1px solid var(--line);
  border-radius: var(--r-lg); padding: 34px 32px; box-shadow: var(--shadow-2); }
.mark { width: 44px; height: 44px; border-radius: 10px; background: var(--brand); color: #fff;
  display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 15px; margin-bottom: 16px; }
h1 { font-size: 20px; margin: 0 0 4px; }
.sub { font-size: 13px; color: var(--ink-3); margin: 0 0 22px; }
.form { display: flex; flex-direction: column; gap: 14px; }
.field { display: flex; flex-direction: column; gap: 5px; font-size: 12.5px; color: var(--ink-2); }
.field input { font: inherit; font-size: 14px; padding: 10px 12px; border: 1px solid var(--line-strong);
  border-radius: var(--r-sm); background: var(--paper); color: var(--ink); }
.field input:focus { outline: none; border-color: var(--brand); background: var(--surface); }
.err { font-size: 12.5px; color: var(--danger); background: var(--danger-soft);
  padding: 8px 11px; border-radius: var(--r-sm); }
.btn { margin-top: 4px; padding: 11px; border: none; border-radius: var(--r-sm);
  background: var(--brand); color: #fff; font: inherit; font-weight: 600; cursor: pointer;
  transition: background 120ms ease-out; }
.btn:hover:not(:disabled) { background: var(--brand-strong); }
.btn:active:not(:disabled) { transform: translateY(1px); }
.btn:disabled { opacity: .5; cursor: not-allowed; }
</style>
