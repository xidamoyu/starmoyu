<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ChatDotRound, Notebook, User, DocumentChecked, Upload, Setting, SwitchButton, ArrowDown } from '@element-plus/icons-vue'
import { logout, api } from '../api'
import { useChatStore } from '../stores/chat'

const route = useRoute()
const router = useRouter()
const store = useChatStore()

const role = ref<string>('')
const displayName = ref<string>('')
onMounted(async () => {
  try {
    const r = await api.get('/me')
    role.value = r.data.role
    displayName.value = r.data.user_id
  } catch { /* 401 已由拦截器处理 */ }
  store.refreshConversations().catch(() => {})
})
const isAdmin = computed(() => role.value === 'admin')
const roleLabel = computed(() => ({ admin: '管理员', operator: '运营', viewer: '只读' })[role.value] ?? role.value)

// 「对话」分组 = 会话列表（点击切换会话并跳到对话页）
const conversations = computed(() => store.convList ?? [])
async function pickConv(id: string) {
  store.select(id)
  if (route.path !== '/') router.push('/')
}
function newChat() {
  store.startNew()
  router.push('/')
}

const activePath = computed(() => route.path)
</script>

<template>
  <div class="layout">
    <!-- 顶栏 -->
    <header class="topbar">
      <div class="brand"><span class="logo">★</span><span>星图商单助手</span></div>
      <div class="top-right">
        <span class="role-tag">{{ roleLabel }}</span>
        <el-dropdown>
          <span class="user-chip">
            <span class="avatar">{{ (displayName || '?').slice(0, 1).toUpperCase() }}</span>
            <span class="uname">{{ displayName }}</span>
            <el-icon><ArrowDown /></el-icon>
          </span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item @click="logout">
                <el-icon><SwitchButton /></el-icon>退出登录
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </header>

    <div class="body">
      <!-- 侧边栏：分组折叠 -->
      <aside class="nav">
        <div class="group">
          <div class="group-title">对话</div>
          <button class="nav-item new-chat" @click="newChat">
            <el-icon><ChatDotRound /></el-icon><span>新对话</span>
          </button>
          <button v-for="c in conversations.slice(0, 8)" :key="c.conv_id"
                  class="nav-item conv" :class="{ active: route.path === '/' && store.convId === c.conv_id }"
                  :title="c.title" @click="pickConv(c.conv_id)">
            <span class="dot"></span><span class="conv-title">{{ c.title }}</span>
          </button>
        </div>

        <div class="group">
          <div class="group-title">资料库</div>
          <router-link to="/kols" class="nav-item" :class="{ active: activePath === '/kols' }">
            <el-icon><User /></el-icon><span>达人库</span>
          </router-link>
          <router-link to="/deals" class="nav-item" :class="{ active: activePath === '/deals' }">
            <el-icon><Notebook /></el-icon><span>商单台账</span>
          </router-link>
          <router-link to="/proposals" class="nav-item" :class="{ active: activePath === '/proposals' }">
            <el-icon><DocumentChecked /></el-icon><span>审批中心</span>
          </router-link>
        </div>

        <div class="group">
          <div class="group-title">设置</div>
          <router-link to="/import" class="nav-item" :class="{ active: activePath === '/import' }">
            <el-icon><Upload /></el-icon><span>导入管理</span>
          </router-link>
          <router-link v-if="isAdmin" to="/users" class="nav-item" :class="{ active: activePath === '/users' }">
            <el-icon><Setting /></el-icon><span>用户管理</span>
          </router-link>
        </div>
      </aside>

      <!-- 主区域 -->
      <main class="main">
        <router-view />
      </main>
    </div>
  </div>
</template>

<style scoped>
.layout { display: flex; flex-direction: column; height: 100vh; }

/* 顶栏 */
.topbar {
  height: 54px; flex: none; display: flex; align-items: center; justify-content: space-between;
  padding: 0 20px; background: var(--surface); border-bottom: 1px solid var(--line);
}
.brand { display: flex; align-items: center; gap: 8px; font-weight: 700; font-size: 15px; color: var(--ink); }
.logo { color: #007bff; font-size: 17px; }
.top-right { display: flex; align-items: center; gap: 12px; }
.role-tag {
  font-size: 12px; color: #007bff; background: rgba(0, 123, 255, .08);
  padding: 3px 10px; border-radius: 10px;
}
.user-chip { display: flex; align-items: center; gap: 8px; cursor: pointer; outline: none; }
.avatar {
  width: 28px; height: 28px; border-radius: 50%; background: #007bff; color: #fff;
  display: inline-flex; align-items: center; justify-content: center; font-size: 13px; font-weight: 600;
}
.uname { font-size: 13.5px; color: var(--ink); }

.body { display: flex; flex: 1; min-height: 0; }

/* 侧边栏：浅灰底 + 分组 */
.nav {
  width: 216px; flex: none; background: var(--surface-2, #f5f5f5); border-right: 1px solid var(--line);
  overflow-y: auto; padding: 12px 10px; display: flex; flex-direction: column; gap: 14px;
}
.group { display: flex; flex-direction: column; gap: 2px; }
.group-title { font-size: 11.5px; color: var(--ink-3, #909399); padding: 0 10px 4px; letter-spacing: 1px; }
.nav-item {
  display: flex; align-items: center; gap: 9px; padding: 8px 12px; border: none;
  background: none; border-radius: 6px; cursor: pointer; text-decoration: none;
  color: var(--ink-2, #303133); font: inherit; font-size: 13.5px; text-align: left;
  width: 100%; box-sizing: border-box; white-space: nowrap;
}
.nav-item:hover { background: rgba(0, 123, 255, .08); }
.nav-item.active { background: #007bff; color: #fff; }
.new-chat { color: #007bff; font-weight: 500; }
.conv .dot { width: 6px; height: 6px; border-radius: 50%; background: #c0c4cc; flex: none; }
.conv.active .dot { background: #fff; }
.conv-title { overflow: hidden; text-overflow: ellipsis; }

/* 主区域 */
.main { flex: 1; min-width: 0; overflow-y: auto; background: var(--paper); }

/* 窄屏：侧边栏折叠为图标 rail */
@media (max-width: 768px) {
  .nav { width: 52px; padding: 12px 6px; }
  .nav-item { justify-content: center; padding: 10px 0; gap: 0; }
  .nav-item span, .group-title, .conv .dot { display: none; }
  .role-tag { display: none; }
}
</style>
