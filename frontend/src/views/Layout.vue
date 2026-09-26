<script setup lang="ts">
import { ref } from 'vue'
import { useRoute } from 'vue-router'
import { ChatDotRound, Notebook, User, DocumentChecked, Upload } from '@element-plus/icons-vue'

const route = useRoute()
const navs = [
  { path: '/', label: '对话助手', icon: ChatDotRound },
  { path: '/deals', label: '商单台账', icon: Notebook },
  { path: '/kols', label: '达人库', icon: User },
  { path: '/proposals', label: '审批中心', icon: DocumentChecked },
  { path: '/import', label: '导入管理', icon: Upload },
]
</script>

<template>
  <el-container class="layout">
    <el-aside width="200px" class="nav">
      <div class="brand">星图商单助手</div>
      <div
        v-for="n in navs"
        :key="n.path"
        class="nav-item"
        :class="{ active: route.path === n.path }"
        @click="$router.push(n.path)"
      >
        <el-icon><component :is="n.icon" /></el-icon>
        <span>{{ n.label }}</span>
      </div>
      <div class="foot">M2 · Vue3</div>
    </el-aside>
    <el-main class="main">
      <router-view />
    </el-main>
  </el-container>
</template>

<style scoped>
.layout { height: 100vh; }
.nav {
  display: flex; flex-direction: column;
  border-right: 1px solid var(--el-border-color-light); padding: 12px 8px;
}
.brand { font-weight: 700; font-size: 15px; padding: 8px 10px 16px; }
.nav-item {
  display: flex; align-items: center; gap: 8px;
  padding: 10px 12px; border-radius: 6px; cursor: pointer;
  color: var(--el-text-color-regular); font-size: 14px;
}
.nav-item:hover { background: var(--el-fill-color-light); }
.nav-item.active { background: var(--el-color-primary-light-9); color: var(--el-color-primary); }
.foot { margin-top: auto; text-align: center; font-size: 11px; color: var(--el-text-color-secondary); }
.main { padding: 0; background: var(--el-fill-color-lighter); }
</style>

<style>
body { margin: 0; font-family: 'Helvetica Neue', Arial, 'PingFang SC', 'Microsoft YaHei', sans-serif; }
</style>
