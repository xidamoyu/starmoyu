<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { login } from '../api'

const router = useRouter()
const username = ref('admin')
const password = ref('')
const loading = ref(false)

async function submit() {
  loading.value = true
  try {
    await login(username.value, password.value)
    router.push('/')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-wrap">
    <el-card class="login-card">
      <h2>星图商单助手</h2>
      <p class="sub">对话式 MCN 业务助手</p>
      <el-form @submit.prevent="submit">
        <el-form-item>
          <el-input v-model="username" placeholder="用户名" />
        </el-form-item>
        <el-form-item>
          <el-input v-model="password" type="password" placeholder="密码" show-password />
        </el-form-item>
        <el-button type="primary" :loading="loading" class="btn" native-type="submit">
          登录
        </el-button>
      </el-form>
    </el-card>
  </div>
</template>

<style scoped>
.login-wrap {
  height: 100vh; display: flex; align-items: center; justify-content: center;
  background: var(--el-fill-color-light);
}
.login-card { width: 360px; text-align: center; }
.login-card h2 { margin: 8px 0 4px; }
.sub { color: var(--el-text-color-secondary); font-size: 13px; margin-bottom: 20px; }
.btn { width: 100%; }
</style>
