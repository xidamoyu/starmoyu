<script setup lang="ts">
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
  <div class="layout">
    <aside class="nav">
      <div class="brand">星图商单助手</div>
      <nav>
        <router-link v-for="n in navs" :key="n.path" :to="n.path" class="nav-item"
                     :class="{ active: route.path === n.path }">
          <el-icon><component :is="n.icon" /></el-icon>
          <span>{{ n.label }}</span>
        </router-link>
      </nav>
      <div class="foot">MCN 业务工作台</div>
    </aside>
    <main class="main">
      <router-view />
    </main>
  </div>
</template>

<style scoped>
.layout { display: flex; height: 100vh; }
.nav { width: 208px; flex: none; display: flex; flex-direction: column;
  border-right: 1px solid var(--line); background: var(--surface); padding: 14px 10px;
  transition: width 180ms ease-out; }
.brand { font-weight: 700; font-size: 15px; padding: 6px 12px 18px; color: var(--ink); white-space: nowrap; }
.nav nav { display: flex; flex-direction: column; gap: 2px; }
.nav-item { display: flex; align-items: center; gap: 9px; padding: 9px 12px;
  border-radius: var(--r-sm); color: var(--ink-2); font-size: 13.5px; text-decoration: none;
  border-left: 3px solid transparent; transition: background 120ms ease-out, color 120ms ease-out;
  white-space: nowrap; }
.nav-item:hover { background: var(--surface-2); color: var(--ink); }
.nav-item.active { background: var(--brand-soft); color: var(--brand-strong);
  font-weight: 600; border-left-color: var(--brand); }
.foot { margin-top: auto; text-align: center; font-size: 11px; color: var(--ink-3); white-space: nowrap; }
.main { flex: 1; min-width: 0; background: var(--paper); overflow: hidden; }

/* 窄屏：侧边栏折叠为图标 rail，主区恢复宽度（修 stage badge 被挤掉） */
@media (max-width: 768px) {
  .nav { width: 52px; padding: 14px 6px; }
  .brand { visibility: hidden; height: 20px; padding: 0 0 14px; position: relative; }
  .brand::after { content: '星'; visibility: visible; position: absolute; left: 0; right: 0;
    top: 0; text-align: center; font-size: 15px; }
  .nav-item { justify-content: center; padding: 10px 0; gap: 0; border-left: none;
    border-radius: var(--r-sm); }
  .nav-item span { display: none; }
  .nav-item.active { border-left: none; box-shadow: inset 0 0 0 1px var(--brand); }
  .foot { visibility: hidden; }
}
</style>
