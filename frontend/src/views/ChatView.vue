<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Promotion, Plus, ChatDotRound, Link } from '@element-plus/icons-vue'
import { useChatStore } from '../stores/chat'
import { getMessages, getPinned } from '../api'
import Md from '../components/Md.vue'
import DealDrawer from '../components/DealDrawer.vue'

const store = useChatStore()
const input = ref('')
const bodyRef = ref<HTMLElement | null>(null)
const drawerDealId = ref<string | null>(null)

const TOOL_LABELS: Record<string, string> = {
  search_knowledge: '📚 检索知识库',
  search_kols: '👥 检索达人库',
  get_deal_status: '📋 查询商单状态',
  match_kols_for_requirement: '🎯 达人组合匹配',
  create_proposal: '📝 创建方案',
  update_proposal_status: '✔️ 更新方案状态',
  create_followup: '🖊️ 记录跟进',
  save_interaction: '🗂️ 沉淀达人经验',
  list_kol_traits: '🧾 查询达人沉淀',
  save_deal_result: '📦 结案复盘归档',
  get_deal_result: '📊 查询结案效果',
  save_brief: '📥 Brief 接单建档',
  get_today_briefing: '🌅 今日主动简报',
  save_brand_traits: '🏷️ 沉淀品牌特质',
  get_brand_traits: '🏷️ 查询品牌特质',
  get_pending_traits: '⏳ 待确认沉淀',
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
  // 还原本会话钉住的商单（M6）
  try {
    const p = await getPinned(convId)
    store.pinnedDealId = p.pinned_deal_id
  } catch { store.pinnedDealId = null }
  const msgs = await getMessages(convId)
  store.messages = msgs
    .filter((m) => m.role === 'user' || m.role === 'assistant')
    .map((m) => ({ role: m.role as 'user' | 'assistant', text: m.content ?? '' }))
  await scrollBottom()
}

async function newConv() {
  await store.startNew()
}

/** M6: 关联检测到的商单 */
async function linkDetected(dealId: string) {
  await store.setPinned(dealId)
  ElMessage.success(`已关联商单 ${dealId}，本会话的沉淀将自动溯源到它`)
}

function openDrawer(dealId: string) { drawerDealId.value = dealId }

watch(() => store.pinnedDealId, () => scrollBottom())

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
  <div class="chat-page">
    <!-- 会话侧栏 -->
    <aside class="sidebar">
      <button class="new-btn" @click="newConv"><el-icon><Plus /></el-icon> 新对话</button>
      <div class="conv-list">
        <div v-for="c in store.convList" :key="c.conv_id" class="conv-item"
             :class="{ active: c.conv_id === store.convId }"
             @click="loadHistory(c.conv_id)">
          <el-icon><ChatDotRound /></el-icon>
          <span class="conv-title">{{ c.title }}</span>
        </div>
      </div>
    </aside>

    <!-- 对话主区 -->
    <div class="main-col">
      <!-- M6 商单关联提示条 -->
      <div v-for="d in store.detectedDealIds" :key="d" class="link-bar">
        <el-icon><Link /></el-icon>
        <span>检测到商单 <b class="deal" @click="openDrawer(d)">{{ d }}</b>，关联到本会话？沉淀将自动溯源到它。</span>
        <button class="link-btn" @click="linkDetected(d)">关联</button>
        <button class="link-btn ghost" @click="store.dismissDetected(d)">忽略</button>
      </div>

      <!-- 钉住的商单 chip -->
      <div v-if="store.pinnedDealId" class="pinned-chip">
        <span>📌 已关联 <b class="deal" @click="openDrawer(store.pinnedDealId)">{{ store.pinnedDealId }}</b></span>
        <button class="x" @click="store.setPinned(null)" title="取消关联">✕</button>
      </div>

      <!-- 消息体 -->
      <div ref="bodyRef" class="chat-body">
        <template v-for="(m, i) in store.messages" :key="i">
          <div v-if="m.role === 'user'" class="row user">
            <div class="bubble user-bubble">{{ m.text }}</div>
          </div>
          <div v-else class="row assistant">
            <div v-if="m.steps?.length" class="steps">
              <el-collapse>
                <el-collapse-item :title="`🔧 Agent 执行过程（${m.steps.length} 步）`">
                  <div v-for="(s, j) in m.steps" :key="j" class="step" :class="s.kind">
                    <span class="step-label">{{ TOOL_LABELS[s.name] ?? s.name }}</span>
                    <span class="step-detail">{{ s.detail }}</span>
                  </div>
                </el-collapse-item>
              </el-collapse>
            </div>
            <!-- 网格破坏：助手内容物穿透气泡宽度，Markdown 表格/卡片按内容决定宽 -->
            <div class="assistant-content" :class="{ error: m.error }">
              <Md v-if="m.text" :text="m.text" />
              <span v-else-if="store.streaming && i === store.messages.length - 1" class="typing">思考中…</span>
            </div>
          </div>
        </template>
      </div>

      <!-- 输入区 -->
      <div class="chat-footer">
        <div class="input-row">
          <textarea v-model="input" class="input" rows="1"
            placeholder="例如：找几个美妆腰部达人，预算 15 万 / DC20250026 现在什么情况 / 帮我记录一条跟进…"
            @keydown.enter.exact.prevent="send" />
          <button class="send-btn" :disabled="store.streaming" @click="send">
            <el-icon><Promotion /></el-icon> 发送
          </button>
        </div>
        <div class="foot-hint">可查报价规则 · 搜达人 · 查商单 · 记跟进 · 出方案 · 沉淀经验</div>
      </div>
    </div>

    <!-- M6 商单路线图抽屉 -->
    <DealDrawer :deal-id="drawerDealId" @close="drawerDealId = null" />
  </div>
</template>

<style scoped>
.chat-page { display: flex; height: 100vh; }

.sidebar { width: 230px; flex: none; display: flex; flex-direction: column;
  border-right: 1px solid var(--line); background: var(--surface); padding: 12px; }
.new-btn { display: flex; align-items: center; justify-content: center; gap: 6px;
  padding: 9px; border: none; border-radius: var(--r-sm); background: var(--brand);
  color: #fff; font: inherit; font-size: 14px; font-weight: 600; cursor: pointer; margin-bottom: 12px;
  transition: background 120ms ease-out; }
.new-btn:hover { background: var(--brand-strong); }
.new-btn:active { transform: translateY(1px); }
.conv-list { flex: 1; overflow-y: auto; }
.conv-item { display: flex; align-items: center; gap: 8px; padding: 9px 10px;
  border-radius: var(--r-sm); cursor: pointer; color: var(--ink-2); font-size: 13px; }
.conv-item:hover { background: var(--surface-2); }
.conv-item.active { background: var(--brand-soft); color: var(--brand-strong); font-weight: 600; }
.conv-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.main-col { flex: 1; display: flex; flex-direction: column; min-width: 0; }

.link-bar { display: flex; align-items: center; gap: 8px; margin: 10px 16px 0;
  padding: 9px 12px; background: var(--brand-softer); border: 1px solid var(--brand-soft);
  border-radius: var(--r-md); font-size: 13px; color: var(--ink); }
.link-bar .deal { color: var(--brand); font-family: var(--mono); cursor: pointer; }
.link-bar .deal:hover { text-decoration: underline; }
.link-btn { margin-left: auto; padding: 5px 12px; border: none; border-radius: var(--r-sm);
  background: var(--brand); color: #fff; font: inherit; font-size: 12px; cursor: pointer; }
.link-btn.ghost { margin-left: 0; background: transparent; color: var(--ink-3); }
.link-btn.ghost:hover { color: var(--ink); }

.pinned-chip { display: flex; align-items: center; gap: 8px; margin: 10px 16px 0;
  padding: 6px 12px; background: var(--brand-soft); border-radius: 20px;
  font-size: 12.5px; color: var(--brand-strong); width: fit-content; }
.pinned-chip .deal { font-family: var(--mono); cursor: pointer; font-weight: 600; }
.pinned-chip .deal:hover { text-decoration: underline; }
.pinned-chip .x { border: none; background: transparent; color: var(--brand-strong);
  cursor: pointer; font-size: 12px; padding: 0 2px; border-radius: 4px; }
.pinned-chip .x:hover { background: rgba(15,76,92,.12); }

.chat-body { flex: 1; overflow-y: auto; padding: 16px 20px; }
.row { display: flex; margin: 14px 0; }
.row.user { justify-content: flex-end; }
.row.assistant { justify-content: flex-start; flex-direction: column; }

.bubble { max-width: 76%; padding: 10px 14px; border-radius: var(--r-md); line-height: 1.55; font-size: 14px; }
.user-bubble { background: var(--brand); color: #fff; border-bottom-right-radius: 3px; white-space: pre-wrap; }

.assistant-content { background: var(--surface); border: 1px solid var(--line);
  border-radius: var(--r-md); border-bottom-left-radius: 3px; padding: 12px 16px;
  width: fit-content; max-width: 92%; box-shadow: var(--shadow-1); }
.assistant-content.error { border-color: var(--danger); }
.typing { color: var(--ink-3); }

.steps { max-width: 92%; margin-bottom: 6px; }
.steps :deep(.el-collapse-item__header) { font-size: 12px; color: var(--ink-3); border: none; }
.steps :deep(.el-collapse) { border: none; }
.step { display: flex; gap: 8px; padding: 4px 0; font-size: 12px;
  border-bottom: 1px dashed var(--line); }
.step:last-child { border-bottom: none; }
.step.tool_call .step-label { color: var(--brand); font-weight: 600; }
.step.tool_result .step-label { color: var(--ok); font-weight: 600; }
.step-detail { color: var(--ink-3); overflow: hidden; text-overflow: ellipsis; }

.chat-footer { border-top: 1px solid var(--line); background: var(--surface); padding: 12px 16px 8px; }
.input-row { display: flex; gap: 10px; align-items: flex-end; }
.input { flex: 1; resize: none; font: inherit; font-size: 14px; line-height: 1.5;
  padding: 10px 12px; border: 1px solid var(--line-strong); border-radius: var(--r-md);
  background: var(--paper); color: var(--ink); }
.input:focus { outline: none; border-color: var(--brand); background: var(--surface); }
.send-btn { display: flex; align-items: center; gap: 6px; padding: 9px; border: none;
  border-radius: var(--r-sm); background: var(--brand); color: #fff; font: inherit;
  font-weight: 600; cursor: pointer; transition: background 120ms ease-out; }
.send-btn:hover:not(:disabled) { background: var(--brand-strong); }
.send-btn:active:not(:disabled) { transform: translateY(1px); }
.send-btn:disabled { opacity: .45; cursor: not-allowed; }
.foot-hint { font-size: 11px; color: var(--ink-3); margin-top: 6px; text-align: center; }
</style>
