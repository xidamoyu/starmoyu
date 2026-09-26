<script setup lang="ts">
import { ref } from 'vue'
import { api } from '../api'
import { ElMessage } from 'element-plus'

const file = ref<File | null>(null)
const kind = ref('kols')
const uploading = ref(false)
const result = ref<{ rows_parsed: number; inserted: number } | null>(null)

function onFile(f: File) {
  file.value = f
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
    <el-card>
      <h3>批量导入达人</h3>
      <p class="hint">
        支持 .xlsx / .csv。列名需包含 kol_name（达人昵称）、platform、category、tier、
        fans_count、interact_rate、price_21_60s、avg_views；kol_id 可选（缺省自动生成，重复自动跳过）。
      </p>
      <el-space direction="vertical" alignment="flex-start" :size="14">
        <el-select v-model="kind" style="width:160px">
          <el-option label="达人库" value="kols" />
        </el-select>
        <el-upload
          :auto-upload="false"
          :limit="1"
          accept=".xlsx,.csv"
          :on-change="(f: any) => onFile(f.raw)"
          drag
        >
          <el-icon class="el-icon--upload"><upload-filled /></el-icon>
          <div class="el-upload__text">拖拽文件或 <em>点击选择</em></div>
        </el-upload>
        <el-button type="primary" :loading="uploading" :disabled="!file" @click="upload">
          开始导入
        </el-button>
        <el-alert
          v-if="result"
          :title="`解析 ${result.rows_parsed} 行，实际落库 ${result.inserted} 条（重复 kol_id 自动跳过）`"
          type="success"
          :closable="false"
        />
      </el-space>
    </el-card>
  </div>
</template>

<script lang="ts">
import { UploadFilled } from '@element-plus/icons-vue'
export default { components: { UploadFilled } }
</script>

<style scoped>
.page { padding: 16px; max-width: 640px; }
.hint { font-size: 12px; color: var(--el-text-color-secondary); line-height: 1.8; }
</style>
