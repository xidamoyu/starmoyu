<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Promotion, Plus, ChatDotRound } from '@element-plus/icons-vue'
import { useChatStore } from '../stores/chat'
import { getMessages } from '../api'

const store = useChatStore()
const input = ref('')
const bodyRef = ref<HTMLElement | null>(null)

const TOOL_LABELS: Record<string, string> = {
  search_knowledge: '📚 检索知识库',
  search_kols: '👥 检索达人库',
  get_deal_status: '📋 查询商单状态',
  match_kols_for_requirement: '🎯 达人组合匹配',
  create_proposal: '📝 创建方案',
  update_proposal_status: '✔️ 更新方案状态',
  create_followup: '🖊️ 记录跟进',
}

async function scrollBottom() {
  await nextTick()
  bodyRef.value?.scrollTo({ top: bodyRef.value.scrollHeight })
}

async function send() {
  const t = input.value.trim()
  if (!t) return
  input.value = ''
  await store.send(t)
  await scrollBottom()
}

async function loadHistory(convId: string) {
  store.select(convId)
  const msgs = await getMessages(convId)
  store.messages = msgs
    .filter((m) => m.role === 'user' || m.role === 'assistant')
    .map((m) => ({ role: m.role as 'user' | 'assistant', text: m.content ?? '' }))
  await scrollBottom()
}

async function newConv() {
  await store.startNew()
}

onMounted(async () => {
  try {
    await store.refreshConversations()
    await store.startNew()
  } catch { /* 401 已由拦截器处理 */ }
  if (!localStorage.getItem('token')) {
    ElMessage.warning('请先登录')
    location.hash = '#/login'
  }
})
</script>

<template>
  <el-container class="chat-page">
    <!-- 会话侧栏 -->
    <el-aside width="240px" class="sidebar">
      <el-button type="primary" :icon="Plus" class="new-btn" @click="newConv">
        新对话
      </el-button>
      <div class="conv-list">
        <div
          v-for="c in store.convList"
          :key="c.conv_id"
          class="conv-item"
          :class="{ active: c.conv_id === store.convId }"
          @click="loadHistory(c.conv_id)"
        >
          <el-icon><ChatDotRound /></el-icon>
          <span class="conv-title">{{ c.title }}</span>
        </div>
      </div>
      <div class="sidebar-foot">星图商单助手 · M1</div>
    </el-aside>

    <!-- 对话主区 -->
    <el-container>
      <el-header class="chat-header">
        <b>对话助手</b>
        <span class="hint">可查报价规则 / 搜达人 / 查商单 / 记跟进 / 出方案</span>
      </el-header>

      <el-main ref="bodyRef" class="chat-body">
        <template v-for="(m, i) in store.messages" :key="i">
          <!-- 用户消息 -->
          <div v-if="m.role === 'user'" class="row user">
            <div class="bubble user-bubble">{{ m.text }}</div>
          </div>

          <!-- 助手消息 -->
          <div v-else class="row assistant">
            <!-- 工具调用过程卡片 -->
            <div v-if="m.steps?.length" class="steps">
              <el-collapse>
                <el-collapse-item :title="`🔧 Agent 执行过程（${m.steps.length} 步）`">
                  <div
                    v-for="(s, j) in m.steps"
                    :key="j"
                    class="step"
                    :class="s.kind"
                  >
                    <span class="step-label">{{ TOOL_LABELS[s.name] ?? s.name }}</span>
                    <span class="step-detail">{{ s.detail }}</span>
                  </div>
                </el-collapse-item>
              </el-collapse>
            </div>
            <div class="bubble assistant-bubble" :class="{ error: m.error }">
              <span v-if="m.text" class="pre-wrap">{{ m.text }}</span>
              <span v-else-if="store.streaming && i === store.messages.length - 1" class="typing">
                思考中…
              </span>
            </div>
          </div>
        </template>
      </el-main>

      <el-footer class="chat-footer" height="auto">
        <div class="input-row">
          <el-input
            v-model="input"
            type="textarea"
            :autosize="{ minRows: 1, maxRows: 4 }"
            placeholder="例如：找几个美妆腰部达人，预算 15 万 / DC20250026 现在什么情况 / 帮我记录一条跟进…"
            @keydown.enter.exact.prevent="send"
          />
          <el-button
            type="primary"
            :icon="Promotion"
            :loading="store.streaming"
            @click="send"
          >
            发送
          </el-button>
        </div>
      </el-footer>
    </el-container>
  </el-container>
</template>

<style scoped>
.chat-page { height: 100vh; }
.sidebar {
  display: flex; flex-direction: column;
  border-right: 1px solid var(--el-border-color-light);
  padding: 12px;
}
.new-btn { width: 100%; margin-bottom: 12px; }
.conv-list { flex: 1; overflow-y: auto; }
.conv-item {
  display: flex; align-items: center; gap: 8px;
  padding: 10px; border-radius: 6px; cursor: pointer;
  color: var(--el-text-color-regular); font-size: 13px;
}
.conv-item:hover { background: var(--el-fill-color-light); }
.conv-item.active { background: var(--el-color-primary-light-9); color: var(--el-color-primary); }
.conv-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sidebar-foot { font-size: 11px; color: var(--el-text-color-secondary); text-align: center; }

.chat-header {
  display: flex; align-items: baseline; gap: 12px;
  border-bottom: 1px solid var(--el-border-color-light);
}
.chat-header .hint { font-size: 12px; color: var(--el-text-color-secondary); }

.chat-body { overflow-y: auto; background: var(--el-fill-color-lighter); }
.row { display: flex; margin: 12px 0; }
.row.user { justify-content: flex-end; }
.row.assistant { justify-content: flex-start; flex-direction: column; }

.bubble {
  max-width: 76%; padding: 10px 14px; border-radius: 10px;
  line-height: 1.6; font-size: 14px;
}
.user-bubble {
  background: var(--el-color-primary); color: #fff;
  border-bottom-right-radius: 2px; white-space: pre-wrap;
}
.assistant-bubble {
  background: #fff; border: 1px solid var(--el-border-color-light);
  border-bottom-left-radius: 2px; white-space: pre-wrap;
}
.assistant-bubble.error { border-color: var(--el-color-danger); }
.typing { color: var(--el-text-color-secondary); }

.steps { max-width: 76%; margin-bottom: 6px; }
.steps :deep(.el-collapse-item__header) { font-size: 12px; color: var(--el-text-color-secondary); }
.step {
  display: flex; gap: 8px; padding: 4px 0;
  font-size: 12px; border-bottom: 1px dashed var(--el-border-color-lighter);
}
.step:last-child { border-bottom: none; }
.step.tool_call .step-label { color: var(--el-color-primary); font-weight: 600; }
.step.tool_result .step-label { color: var(--el-color-success); font-weight: 600; }
.step-detail { color: var(--el-text-color-secondary); overflow: hidden; text-overflow: ellipsis; }

.chat-footer { border-top: 1px solid var(--el-border-color-light); padding: 12px; }
.input-row { display: flex; gap: 10px; align-items: flex-end; }
.pre-wrap { white-space: pre-wrap; }
</style>
