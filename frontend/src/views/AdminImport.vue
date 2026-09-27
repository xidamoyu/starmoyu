<script setup lang="ts">
import { ref } from 'vue'
import { api } from '../api'
import { ElMessage } from 'element-plus'

const file = ref<File | null>(null)
const kind = ref('kols')
const uploading = ref(false)
const result = ref<{ rows_parsed: number; inserted: number } | null>(null)
const dragOver = ref(false)

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
    ElMessage.success(`导入完成：解析 ${r.data.rows_parsed} 行，落库 ${r.data.inserted} 条`)
  } finally {
    uploading.value = false
  }
}
</script>

<template>
  <div class="page">
    <div class="title">导入管理</div>
    <div class="card">
      <div class="card-head">批量导入达人</div>
      <p class="hint">
        支持 .xlsx / .csv。列名需包含 kol_name（达人昵称）、platform、category、tier、
        fans_count、interact_rate、price_21_60s、avg_views；kol_id 可选（缺省自动生成，重复自动跳过）。
      </p>

      <div class="drop" :class="{ over: dragOver }"
           @dragover.prevent="dragOver = true" @dragleave="dragOver = false" @drop.prevent="onDrop">
        <el-icon class="up-icon"><upload-filled /></el-icon>
        <div class="up-text">
          <template v-if="file">{{ file.name }}</template>
          <template v-else>拖拽文件到这里，或 <label class="pick">点击选择<input type="file" accept=".xlsx,.csv" class="hidden" @change="(e:any) => onFile(e.target.files?.[0])" /></label></template>
        </div>
      </div>

      <div class="row">
        <select v-model="kind" class="select">
          <option value="kols">达人库</option>
        </select>
        <button class="btn" :disabled="!file || uploading" @click="upload">
          {{ uploading ? '导入中…' : '开始导入' }}
        </button>
      </div>

      <div v-if="result" class="result">
        解析 {{ result.rows_parsed }} 行，实际落库 {{ result.inserted }} 条（重复 kol_id 自动跳过）
      </div>
    </div>
  </div>
</template>

<script lang="ts">
import { UploadFilled } from '@element-plus/icons-vue'
export default { components: { UploadFilled } }
</script>

<style scoped>
.page { padding: 18px 20px; max-width: 620px; }
.title { font-size: 18px; font-weight: 700; margin-bottom: 16px; }
.card { background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-lg);
  padding: 22px; box-shadow: var(--shadow-1); }
.card-head { font-size: 15px; font-weight: 700; margin-bottom: 8px; }
.hint { font-size: 12px; color: var(--ink-3); line-height: 1.8; margin: 0 0 18px; }
.drop { border: 2px dashed var(--line-strong); border-radius: var(--r-md); padding: 34px 20px;
  text-align: center; color: var(--ink-3); transition: border-color 120ms ease-out, background 120ms ease-out; }
.drop.over { border-color: var(--brand); background: var(--brand-softer); }
.up-icon { font-size: 34px; color: var(--ink-3); margin-bottom: 8px; }
.up-text { font-size: 13px; }
.pick { color: var(--brand); cursor: pointer; font-weight: 600; }
.hidden { display: none; }
.row { display: flex; gap: 10px; margin-top: 16px; align-items: center; }
.select { font: inherit; font-size: 13px; padding: 8px 11px; border: 1px solid var(--line-strong);
  border-radius: var(--r-sm); background: var(--surface); color: var(--ink); }
.btn { padding: 8px 18px; border: none; border-radius: var(--r-sm); background: var(--brand);
  color: #fff; font: inherit; font-weight: 600; cursor: pointer; transition: background 120ms ease-out; }
.btn:hover:not(:disabled) { background: var(--brand-strong); }
.btn:disabled { opacity: .45; cursor: not-allowed; }
.result { margin-top: 16px; font-size: 13px; color: var(--ok); background: var(--ok-soft);
  padding: 10px 14px; border-radius: var(--r-sm); }
</style>
