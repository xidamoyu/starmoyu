<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'

const file = ref<File | null>(null)
const kind = ref('kols')
const uploading = ref(false)
const result = ref<any>(null)
const dragOver = ref(false)

// 暂存区
interface StagingItem {
  staging_id: string; suggested_kind: string; source_type: string
  raw_preview: string; status: string; extracted: any
  reviewed_by?: string | null; created_at: string
  agent_review?: { verdict: string; issues: string[]; notes: string } | null
}
const stagingItems = ref<StagingItem[]>([])
const stagingLoading = ref(false)
const reviewingId = ref('')

const KINDS = [
  { value: 'kols', label: '达人库', hint: '列名需包含 kol_name、platform、category、tier、fans_count、interact_rate、price_21_60s、avg_views；kol_id 可选。低风险：管理员直导，重复自动跳过。' },
  { value: 'deals', label: '商单台账', hint: '列名建议：deal_id（可缺省自动生成）、brand_name、category、goal、budget、stage、demand_desc、start_date、end_date、owner。导入后进入暂存区，Agent 预审 + 管理员确认双关后才写入正式台账。' },
  { value: 'docs', label: '非结构化文档', hint: '支持 .txt / .md / .csv 文本文件（业务笔记、谈判记录、方法论、SOP…）。整篇入暂存区，双审确认后自动切块向量化进知识库，对话立即可检索。' },
]
const kindHint = () => KINDS.find((k) => k.value === kind.value)?.hint ?? ''
const KIND_LABEL: Record<string, string> = {
  kols: '达人库', deals: '商单台账', docs: '非结构化文档',
  import_deal: '导入·商单', import_doc: '导入·文档',
  kol: '达人', feedback: '商单反馈',
}

function onFile(f: File | null) {
  if (f) file.value = f
}
function onDrop(e: DragEvent) {
  dragOver.value = false
  const f = e.dataTransfer?.files?.[0]
  if (f) onFile(f)
}

async function upload() {
  if (!file.value) return
  uploading.value = true
  try {
    const fd = new FormData()
    fd.append('file', file.value)
    const r = await api.post('/admin/import', fd, { params: { kind: kind.value } })
    result.value = r.data
    if (r.data.staged) {
      ElMessage.success(`${r.data.staged} 条已入暂存区，请完成下方双审`)
      await loadStaging()
    } else {
      ElMessage.success(`导入完成：解析 ${r.data.rows_parsed} 行，落库 ${r.data.inserted} 条`)
    }
    file.value = null
  } finally {
    uploading.value = false
  }
}

async function loadStaging() {
  stagingLoading.value = true
  try {
    const r = await api.get('/admin/staging', { params: { status: 'pending' } })
    stagingItems.value = (r.data.items ?? []).map((x: any) => ({
      ...x,
      extracted: typeof x.extracted === 'string' ? safeParse(x.extracted) : x.extracted,
    }))
  } finally {
    stagingLoading.value = false
  }
}
function safeParse(s: string) { try { return JSON.parse(s) } catch { return s } }

/** Agent 预审 */
async function agentReview(item: StagingItem) {
  reviewingId.value = item.staging_id
  try {
    const r = await api.post('/admin/staging/agent-review', { staging_id: item.staging_id })
    item.agent_review = r.data.review
    ElMessage.success(`Agent 预审完成：${r.data.review.verdict}`)
  } finally {
    reviewingId.value = ''
  }
}

/** 管理员确认（含修正字段） */
async function confirmItem(item: StagingItem) {
  const ar = item.agent_review
  if (ar?.verdict === 'reject') {
    await ElMessageBox.confirm(
      `Agent 预审结论为「拒绝」：${(ar.issues ?? []).join('；') || ar.notes}。仍要强制入库吗？`,
      '预审不通过', { confirmButtonText: '强制入库', cancelButtonText: '取消', type: 'warning' })
  }
  // 商单行允许修正字段；文档直接确认
  let finalFields: any = {}
  if (item.suggested_kind === 'import_deal' && item.extracted && typeof item.extracted === 'object') {
    const { value: edited } = await ElMessageBox.prompt(
      '可修正字段后确认入库（JSON 格式）', '确认导入商单',
      { inputValue: JSON.stringify(item.extracted, null, 0),
        confirmButtonText: '确认入库', cancelButtonText: '取消',
        type: ar?.verdict === 'pass' ? 'info' : 'warning' })
    try { finalFields = JSON.parse(edited) } catch { ElMessage.error('JSON 格式错误'); return }
  } else {
    await ElMessageBox.confirm('确认将该文档切块向量化进知识库？', '确认导入文档')
  }
  const r = await api.post('/admin/staging/confirm',
    { staging_id: item.staging_id, final_fields: finalFields })
  ElMessage.success(`已入库：${r.data.written_id ?? r.data.kind}`)
  await loadStaging()
}

/** 驳回 */
async function rejectItem(item: StagingItem) {
  await api.post('/admin/staging/reject', { staging_id: item.staging_id })
  ElMessage.info('已驳回')
  await loadStaging()
}

const verdictCls: Record<string, string> = { pass: 'v-pass', warn: 'v-warn', reject: 'v-reject' }
const verdictLabel: Record<string, string> = { pass: '通过', warn: '有疑问', reject: '拒绝' }

onMounted(loadStaging)
</script>

<template>
  <div class="page">
    <div class="title">导入管理</div>

    <!-- 导入卡片 -->
    <div class="card">
      <div class="card-head">批量导入</div>
      <div class="kind-tabs">
        <a v-for="k in KINDS" :key="k.value" class="ktab" :class="{ on: kind === k.value }"
           @click="kind = k.value">{{ k.label }}</a>
      </div>
      <p class="hint">{{ kindHint() }}</p>

      <div class="drop" :class="{ over: dragOver }"
           @dragover.prevent="dragOver = true" @dragleave="dragOver = false" @drop.prevent="onDrop">
        <el-icon class="up-icon"><upload-filled /></el-icon>
        <div class="up-text">
          <template v-if="file">{{ file.name }}</template>
          <template v-else>拖拽文件到这里，或 <label class="pick">点击选择<input type="file" class="hidden"
              :accept="kind === 'docs' ? '.txt,.md,.csv' : '.xlsx,.csv'"
              @change="(e:any) => onFile(e.target.files?.[0])" /></label></template>
        </div>
      </div>

      <div class="row">
        <button class="btn" :disabled="!file || uploading" @click="upload">
          {{ uploading ? '导入中…' : '开始导入' }}
        </button>
      </div>

      <div v-if="result" class="result">
        <template v-if="result.staged">{{ result.hint }}</template>
        <template v-else>解析 {{ result.rows_parsed }} 行，实际落库 {{ result.inserted }} 条（重复自动跳过）</template>
      </div>
    </div>

    <!-- 暂存区（双审） -->
    <div class="card">
      <div class="card-head">导入暂存区 <span class="count-badge">{{ stagingItems.length }} 待处理</span></div>
      <p class="hint">商单与文档导入先到暂存区：<b>Agent 预审</b>（数据质量/敏感信息检查）→ <b>管理员确认</b> → 才落正式表 / 知识库。达人库直导不经此区。</p>

      <div v-loading="stagingLoading" class="staging-list">
        <div v-if="!stagingItems.length" class="empty">暂存区为空</div>
        <div v-for="it in stagingItems" :key="it.staging_id" class="st-item">
          <div class="st-head">
            <span class="st-kind">{{ KIND_LABEL[it.suggested_kind] ?? it.suggested_kind }}</span>
            <span class="st-id">{{ it.staging_id }}</span>
            <span class="st-time">{{ (it.created_at ?? '').slice(0, 16).replace('T', ' ') }}</span>
            <span v-if="it.agent_review" class="verdict" :class="verdictCls[it.agent_review.verdict]">
              Agent：{{ verdictLabel[it.agent_review.verdict] }}
            </span>
            <span v-else-if="reviewingId === it.staging_id" class="verdict v-warn">Agent 审查中…</span>
            <span v-else class="verdict v-none">未预审</span>
          </div>
          <div class="st-preview">{{ it.raw_preview }}</div>
          <div v-if="it.agent_review?.issues?.length" class="st-issues">
            <div v-for="(iss, i) in it.agent_review.issues" :key="i" class="issue">⚠ {{ iss }}</div>
            <div class="notes">{{ it.agent_review.notes }}</div>
          </div>
          <div class="st-actions">
            <button class="sbtn" :disabled="reviewingId === it.staging_id" @click="agentReview(it)">
              {{ it.agent_review ? '重新预审' : 'Agent 预审' }}
            </button>
            <button class="sbtn ok" @click="confirmItem(it)">确认入库</button>
            <button class="sbtn danger" @click="rejectItem(it)">驳回</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script lang="ts">
import { UploadFilled } from '@element-plus/icons-vue'
export default { components: { UploadFilled } }
</script>

<style scoped>
.page { padding: 18px 20px; max-width: 760px; overflow-y: auto; height: 100%; }
.title { font-size: 18px; font-weight: 700; margin-bottom: 16px; }
.card { background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-lg);
  padding: 22px; box-shadow: var(--shadow-1); margin-bottom: 18px; }
.card-head { font-size: 15px; font-weight: 700; margin-bottom: 8px; display: flex; align-items: center; gap: 8px; }
.count-badge { font-size: 11.5px; padding: 2px 9px; border-radius: 12px; background: var(--warn-soft); color: var(--warn); font-weight: 700; }
.hint { font-size: 12px; color: var(--ink-3); line-height: 1.8; margin: 0 0 14px; }
.hint b { color: var(--ink-2); }

.kind-tabs { display: flex; gap: 6px; margin-bottom: 12px; }
.ktab { padding: 6px 14px; border: 1px solid var(--line-strong); border-radius: 20px;
  background: var(--surface); color: var(--ink-2); font-size: 12.5px; cursor: pointer; }
.ktab.on { background: var(--brand); border-color: var(--brand); color: #fff; font-weight: 600; }

.drop { border: 2px dashed var(--line-strong); border-radius: var(--r-md); padding: 30px 20px;
  text-align: center; color: var(--ink-3); transition: border-color 120ms ease-out, background 120ms ease-out; }
.drop.over { border-color: var(--brand); background: var(--brand-softer); }
.up-icon { font-size: 32px; color: var(--ink-3); margin-bottom: 8px; }
.up-text { font-size: 13px; }
.pick { color: var(--brand); cursor: pointer; font-weight: 600; }
.hidden { display: none; }
.row { display: flex; gap: 10px; margin-top: 14px; align-items: center; }
.btn { padding: 8px 18px; border: none; border-radius: var(--r-sm); background: var(--brand);
  color: #fff; font: inherit; font-weight: 600; cursor: pointer; }
.btn:hover:not(:disabled) { background: var(--brand-strong); }
.btn:disabled { opacity: .45; cursor: not-allowed; }
.result { margin-top: 14px; font-size: 13px; color: var(--ok); background: var(--ok-soft);
  padding: 10px 14px; border-radius: var(--r-sm); }

/* 暂存区 */
.staging-list { display: flex; flex-direction: column; gap: 10px; max-height: 480px; overflow-y: auto; }
.empty { text-align: center; color: var(--ink-3); padding: 30px 0; font-size: 13px; }
.st-item { border: 1px solid var(--line); border-radius: var(--r-md); padding: 12px 14px; }
.st-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 6px; }
.st-kind { font-size: 12px; font-weight: 700; padding: 2px 9px; border-radius: 4px;
  background: var(--brand-soft); color: var(--brand-strong); }
.st-id { font-family: var(--mono); font-size: 11.5px; color: var(--ink-3); }
.st-time { font-size: 11.5px; color: var(--ink-3); margin-left: auto; }
.verdict { font-size: 11.5px; font-weight: 700; padding: 2px 9px; border-radius: 12px; }
.v-pass { background: var(--ok-soft); color: var(--ok); }
.v-warn { background: var(--warn-soft); color: var(--warn); }
.v-reject { background: var(--danger-soft); color: var(--danger); }
.v-none { background: var(--surface-2); color: var(--ink-3); }
.st-preview { font-size: 12.5px; color: var(--ink-2); background: var(--surface-2);
  border-radius: var(--r-sm); padding: 8px 10px; margin-bottom: 8px; word-break: break-all; }
.st-issues { margin-bottom: 8px; }
.issue { font-size: 12px; color: var(--warn); margin: 2px 0; }
.notes { font-size: 12px; color: var(--ink-3); margin-top: 4px; }
.st-actions { display: flex; gap: 8px; }
.sbtn { font: inherit; font-size: 12.5px; font-weight: 600; padding: 6px 14px;
  border: 1px solid var(--line-strong); border-radius: var(--r-sm); background: var(--surface);
  color: var(--ink-2); cursor: pointer; }
.sbtn:hover:not(:disabled) { border-color: var(--brand); color: var(--brand-strong); }
.sbtn.ok { background: var(--brand); border-color: var(--brand); color: #fff; }
.sbtn.ok:hover { background: var(--brand-strong); color: #fff; }
.sbtn.danger { color: var(--danger); }
.sbtn.danger:hover { border-color: var(--danger); }
.sbtn:disabled { opacity: .5; cursor: wait; }
</style>
