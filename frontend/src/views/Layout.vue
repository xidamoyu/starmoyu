<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ChatDotRound, Collection, Setting, SwitchButton, ArrowDown } from '@element-plus/icons-vue'
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
const roleLabel = computed(() => ({ admin: '管理员', operator: '运营', viewer: '只读' })[role.value] ?? role.value)

const conversations = computed(() => store.convList ?? [])
async function pickConv(id: string) {
  store.select(id)
  if (route.path !== '/') router.push('/')
}
function newChat() {
  store.startNew()
  router.push('/')
}

const libItems = [
  { path: '/kols', label: '达人库' },
  { path: '/deals', label: '商单台账' },
  { path: '/proposals', label: '审批中心' },
]
const settingItems = computed(() => {
  const items = [{ path: '/import', label: '导入管理' }]
  if (role.value === 'admin') items.push({ path: '/users', label: '用户管理' })
  return items
})
function go(path: string) {
  router.push(path)
}
const activePath = computed(() => route.path)
</script>

<template>
  <div class="layout">
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
      <aside class="nav">
        <div class="group">
          <div class="group-title">对话</div>
          <button class="nav-item new-chat" @click="newChat">
            <el-icon><ChatDotRound /></el-icon><span>新对话</span>
          </button>
          <button v-for="c in conversations.slice(0, 10)" :key="c.conv_id"
                  class="nav-item conv" :class="{ active: route.path === '/' && store.convId === c.conv_id }"
                  :title="c.title" @click="pickConv(c.conv_id)">
            <span class="dot"></span><span class="conv-title">{{ c.title }}</span>
          </button>
        </div>

        <div class="spacer"></div>

        <el-popover placement="top-start" trigger="hover" :width="150" popper-class="nav-pop">
          <template #reference>
            <button class="nav-item entry" :class="{ active: ['/kols','/deals','/proposals'].includes(activePath) }">
              <el-icon><Collection /></el-icon><span>资料库</span>
            </button>
          </template>
          <div class="pop-menu">
            <button v-for="it in libItems" :key="it.path" class="pop-item"
                    :class="{ active: activePath === it.path }" @click="go(it.path)">{{ it.label }}</button>
          </div>
        </el-popover>

        <el-popover placement="top-start" trigger="hover" :width="150" popper-class="nav-pop">
          <template #reference>
            <button class="nav-item entry" :class="{ active: ['/import','/users'].includes(activePath) }">
              <el-icon><Setting /></el-icon><span>设置</span>
            </button>
          </template>
          <div class="pop-menu">
            <button v-for="it in settingItems" :key="it.path" class="pop-item"
                    :class="{ active: activePath === it.path }" @click="go(it.path)">{{ it.label }}</button>
          </div>
        </el-popover>
      </aside>

      <main class="main">
        <router-view />
      </main>
    </div>
  </div>
</template>

<style scoped>
.layout { display: flex; flex-direction: column; height: 100vh; }

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

.nav {
  width: 232px; flex: none; background: var(--surface-2, #f5f5f5); border-right: 1px solid var(--line);
  display: flex; flex-direction: column; gap: 2px; overflow-y: auto; padding: 12px 10px;
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
.spacer { flex: 1; }
.entry { font-weight: 500; border: 1px solid rgba(0, 123, 255, .35); background: rgba(0, 123, 255, .05); }
.entry.active { background: #007bff; color: #fff; border-color: #007bff; }

.main { flex: 1; min-width: 0; overflow-y: auto; background: var(--paper); }
</style>

<style>
.nav-pop { padding: 6px !important; }
.nav-pop .pop-menu { display: flex; flex-direction: column; gap: 2px; }
.nav-pop .pop-item {
  display: block; width: 100%; text-align: left; padding: 8px 12px; border: none;
  background: none; border-radius: 5px; cursor: pointer; font: inherit; font-size: 13.5px;
  color: #303133;
}
.nav-pop .pop-item:hover { background: rgba(0, 123, 255, .08); color: #007bff; }
.nav-pop .pop-item.active { background: #007bff; color: #fff; }
</style>
