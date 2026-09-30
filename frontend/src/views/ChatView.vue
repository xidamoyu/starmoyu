<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Promotion, Plus, Link } from '@element-plus/icons-vue'
import { useChatStore } from '../stores/chat'
import Md from '../components/Md.vue'
import DealDrawer from '../components/DealDrawer.vue'

const store = useChatStore()
const input = ref('')
const bodyRef = ref<HTMLElement | null>(null)
const drawerDealId = ref<string | null>(null)
// 图片附件（拖拽/粘贴）
interface Att { name: string; dataUrl: string; dataBase64: string }
const attachments = ref<Att[]>([])
const dragging = ref(false)
// 失焦保留：textarea 内容绑在组件 state（input ref）上，切窗口/切焦点不丢

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
  sediment_case: '♻️ 案例沉淀回流',
  save_trait: '🧩 沉淀合作画像',
}

async function scrollBottom() {
  await nextTick()
  bodyRef.value?.scrollTo({ top: bodyRef.value.scrollHeight })
}

async function send() {
  const t = input.value.trim()
  if (!t && !attachments.value.length) return
  input.value = ''
  const atts = attachments.value
  attachments.value = []
  await store.send(t, atts.length ? atts : undefined)
  await store.refreshConversations()   // 首问后标题可能被后端提炼,拉回新名
  await scrollBottom()
}

/** 输入框随内容行数自动增高（1~8 行） */
function autoGrow(e: Event) {
  const el = e.target as HTMLTextAreaElement
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 8 * 24 + 20) + 'px'
}

function addFiles(files: FileList | File[]) {
  for (const f of Array.from(files)) {
    if (!f.type.startsWith('image/')) continue
    const reader = new FileReader()
    reader.onload = () => {
      const dataUrl = String(reader.result)
      attachments.value.push({ name: f.name, dataUrl,
        dataBase64: dataUrl.slice(dataUrl.indexOf(',') + 1) })
    }
    reader.readAsDataURL(f)
  }
}
function onDrop(e: DragEvent) {
  dragging.value = false
  if (e.dataTransfer?.files.length) addFiles(e.dataTransfer.files)
}
function onPaste(e: ClipboardEvent) {
  if (e.clipboardData?.files.length) { addFiles(e.clipboardData.files); e.preventDefault() }
}
function removeAtt(i: number) { attachments.value.splice(i, 1) }

/** M6: 关联检测到的商单 */
async function linkDetected(dealId: string) {
  await store.setPinned(dealId)
  ElMessage.success(`已关联商单 ${dealId}，本会话的沉淀将自动溯源到它`)
}

function openDrawer(dealId: string) { drawerDealId.value = dealId }

watch(() => store.pinnedDealId, () => scrollBottom())

onMounted(async () => {
  // 只拉会话列表，不自动建新对话；空态由用户选会话或手动新建
  try {
    await store.refreshConversations()
    store.clearSelection()
  } catch { /* 401 已由拦截器处理 */ }
  if (!localStorage.getItem('token')) {
    ElMessage.warning('请先登录')
    location.hash = '#/login'
  }
})
</script>

<template>
  <div class="chat-page">
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
        <!-- 空态：未选会话。引导用户找到新功能入口 -->
        <div v-if="!store.convId" class="empty-state">
          <div class="empty-title">选一个会话，或开启新对话</div>
          <div class="empty-sub">在对话里可以直接查达人、问商单、沉淀经验；提到商单号时右侧会弹出关联提示。</div>
          <div class="empty-caps">
            <span class="cap">💬 对话式查数 / 出方案</span>
            <span class="cap">📌 商单号关联溯源（M6）</span>
            <span class="cap">🗂️ 沉淀达人经验复用</span>
          </div>
          <button class="empty-new" @click="store.startNew()"><el-icon><Plus /></el-icon> 开启新对话</button>
          <div class="empty-hint">左侧选历史会话继续 · 台账/达人/审批在顶部「资料库」</div>
        </div>

        <template v-for="(m, i) in store.messages" :key="i">
          <div v-if="m.role === 'user'" class="row user">
            <div class="bubble user-bubble">
              <div v-if="m.attachments?.length" class="msg-atts">
                <img v-for="(a, j) in m.attachments" :key="j" :src="a.dataUrl" class="msg-att" :alt="a.name" />
              </div>
              <div v-if="m.text">{{ m.text }}</div>
            </div>
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

      <!-- 输入区（拖拽图片到此发送） -->
      <div class="chat-footer"
           @dragover.prevent="dragging = true"
           @dragleave.prevent="dragging = false"
           @drop.prevent="onDrop">
        <!-- 附件预览条 -->
        <div v-if="attachments.length" class="att-row">
          <div v-for="(a, i) in attachments" :key="i" class="att-chip">
            <img :src="a.dataUrl" class="att-thumb" :alt="a.name" />
            <span class="att-name">{{ a.name }}</span>
            <button class="att-x" @click="removeAtt(i)">✕</button>
          </div>
        </div>
        <div v-if="dragging" class="drop-hint">松开以添加图片（聊天记录截图会随消息留档）</div>
        <div class="input-row">
          <textarea v-model="input" class="input" rows="1"
            placeholder="例如：找几个美妆腰部达人，预算 15 万 / DC20250026 现在什么情况 / 帮我记录一条跟进…（可拖入聊天记录截图）"
            @keydown.enter.exact.prevent="send"
            @input="autoGrow" @paste="onPaste" />
          <button class="send-btn" :disabled="store.streaming || (!input.trim() && !attachments.length)" @click="send">
            <el-icon><Promotion /></el-icon> 发送
          </button>
        </div>
        <div class="foot-hint">可查报价规则 · 搜达人 · 查商单 · 记跟进 · 出方案 · 沉淀经验（截图可拖入）</div>
      </div>
    </div>

    <!-- M6 商单路线图抽屉 -->
    <DealDrawer :deal-id="drawerDealId" @close="drawerDealId = null" />
  </div>
</template>

<style scoped>
/* 顶栏已占 54px：这里必须 100%（撑满 main），100vh 会把整页撑出滚动、与侧栏底边错位 */
.chat-page { display: flex; height: 100%; min-height: 0; }


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
.empty-state { display: flex; flex-direction: column; align-items: center; justify-content: center;
  height: 100%; text-align: center; gap: 12px; color: var(--ink); }
.empty-title { font-size: 19px; font-weight: 700; }
.empty-sub { font-size: 13px; color: var(--ink-2); max-width: 420px; line-height: 1.6; }
.empty-caps { display: flex; gap: 8px; flex-wrap: wrap; justify-content: center; margin-top: 4px; }
.cap { font-size: 12px; padding: 6px 12px; background: var(--brand-softer); color: var(--brand-strong);
  border-radius: 20px; font-weight: 600; }
.empty-new { display: flex; align-items: center; gap: 6px; margin-top: 10px; padding: 10px 20px;
  border: none; border-radius: var(--r-sm); background: var(--brand); color: #fff; font: inherit;
  font-weight: 600; cursor: pointer; transition: background 120ms ease-out; }
.empty-new:hover { background: var(--brand-strong); }
.empty-hint { font-size: 11.5px; color: var(--ink-3); margin-top: 6px; }
.row { display: flex; margin: 14px 0; }
.row.user { justify-content: flex-end; }
.row.assistant { justify-content: flex-start; flex-direction: column; }

.bubble { max-width: 76%; padding: 10px 14px; border-radius: var(--r-md); line-height: 1.55; font-size: 14px; }
.user-bubble { background: var(--brand); color: #fff; border-bottom-right-radius: 3px; white-space: pre-wrap; }
.msg-atts { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 6px; }
.msg-att { max-width: 180px; max-height: 140px; border-radius: 6px; border: 2px solid rgba(255,255,255,.4); cursor: zoom-in; }

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

.chat-footer { border-top: 1px solid var(--line); background: var(--surface); padding: 12px 16px 8px; position: relative; }
.chat-footer.drag-over { outline: 2px dashed var(--brand); outline-offset: -4px; }
.drop-hint { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
  background: color-mix(in srgb, var(--brand-soft) 85%, transparent); color: var(--brand-strong);
  font-size: 13px; font-weight: 600; z-index: 2; pointer-events: none; border-radius: 4px; }
.att-row { display: flex; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }
.att-chip { display: flex; align-items: center; gap: 6px; padding: 4px 8px 4px 4px;
  background: var(--surface-2); border: 1px solid var(--line); border-radius: var(--r-sm); }
.att-thumb { width: 34px; height: 34px; object-fit: cover; border-radius: 4px; }
.att-name { font-size: 12px; color: var(--ink-2); max-width: 140px; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; }
.att-x { border: none; background: transparent; color: var(--ink-3); cursor: pointer; font-size: 12px; padding: 2px; }
.att-x:hover { color: var(--danger); }
.input-row { display: flex; gap: 10px; align-items: flex-end; }
.input { flex: 1; resize: none; font: inherit; font-size: 14px; line-height: 1.5;
  padding: 10px 12px; border: 1px solid var(--line-strong); border-radius: var(--r-md);
  background: var(--paper); color: var(--ink); min-height: 44px; max-height: 212px;
  overflow-y: auto; field-sizing: content; }
.input:focus { outline: none; border-color: var(--brand); background: var(--surface); }
.send-btn { display: flex; align-items: center; gap: 6px; padding: 9px; border: none;
  border-radius: var(--r-sm); background: var(--brand); color: #fff; font: inherit;
  font-weight: 600; cursor: pointer; transition: background 120ms ease-out; }
.send-btn:hover:not(:disabled) { background: var(--brand-strong); }
.send-btn:active:not(:disabled) { transform: translateY(1px); }
.send-btn:disabled { opacity: .45; cursor: not-allowed; }
.foot-hint { font-size: 11px; color: var(--ink-3); margin-top: 6px; text-align: center; }
</style>
