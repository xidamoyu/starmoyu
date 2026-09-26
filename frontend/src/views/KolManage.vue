<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { ElMessage } from 'element-plus'

interface Kol {
  kol_id: string; kol_name: string; platform: string; category: string
  sub_category: string; tier: string; fans_count: number
  interact_rate: number; price_21_60s: number; avg_views: number
  exclusive_until: string | null; available_from: string | null; blacklist: string | null
}

const kols = ref<Kol[]>([])
const q = ref('')
const tier = ref('')
const total = ref(0)
const loading = ref(false)

// 档期编辑
const dialog = ref(false)
const editing = ref<Kol | null>(null)
const exUntil = ref('')
const availFrom = ref('')
const blacklist = ref('')

async function load() {
  loading.value = true
  try {
    const r = await api.get('/kols', { params: { q: q.value, tier: tier.value, limit: 100 } })
    kols.value = r.data.items
    total.value = r.data.items.length
  } finally {
    loading.value = false
  }
}

function openEdit(k: Kol) {
  editing.value = k
  exUntil.value = k.exclusive_until ?? ''
  availFrom.value = k.available_from ?? ''
  blacklist.value = k.blacklist ?? ''
  dialog.value = true
}

async function saveEdit() {
  if (!editing.value) return
  await api.patch(`/kols/${editing.value.kol_id}`, {
    exclusive_until: exUntil.value,
    available_from: availFrom.value,
    blacklist: blacklist.value,
  })
  ElMessage.success('档期已更新')
  dialog.value = false
  await load()
}

function fmt(v: number | null) {
  if (v == null) return '-'
  return v >= 10000 ? (v / 10000).toFixed(1) + 'w' : String(v)
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="toolbar">
      <el-input v-model="q" placeholder="搜索达人昵称" clearable style="width:220px" @change="load" />
      <el-select v-model="tier" placeholder="达人层级" clearable style="width:140px" @change="load">
        <el-option label="头部" value="头部" />
        <el-option label="腰部" value="腰部" />
        <el-option label="尾部" value="尾部" />
      </el-select>
      <span class="count">共 {{ total }} 位</span>
    </div>

    <el-table :data="kols" v-loading="loading" height="calc(100vh - 160px)" size="small">
      <el-table-column prop="kol_name" label="达人" min-width="140" fixed />
      <el-table-column prop="platform" label="平台" width="70" />
      <el-table-column prop="category" label="类目" width="90" />
      <el-table-column prop="tier" label="层级" width="70" />
      <el-table-column label="粉丝" width="90">
        <template #default="{ row }">{{ fmt(row.fans_count) }}</template>
      </el-table-column>
      <el-table-column label="互动率" width="80">
        <template #default="{ row }">{{ (row.interact_rate * 100).toFixed(2) }}%</template>
      </el-table-column>
      <el-table-column label="报价21-60s" width="110">
        <template #default="{ row }">{{ fmt(row.price_21_60s) }}</template>
      </el-table-column>
      <el-table-column label="排他期至" width="110">
        <template #default="{ row }">
          <el-tag v-if="row.exclusive_until" type="danger" size="small">{{ row.exclusive_until }}</el-tag>
          <span v-else>-</span>
        </template>
      </el-table-column>
      <el-table-column label="黑名单" width="90">
        <template #default="{ row }">
          <el-tag v-if="row.blacklist" type="warning" size="small">有</el-tag>
          <span v-else>-</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="80" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="openEdit(row)">档期</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialog" :title="`档期管理 - ${editing?.kol_name ?? ''}`" width="420px">
      <el-form label-width="90px">
        <el-form-item label="排他期至">
          <el-date-picker v-model="exUntil" type="date" value-format="YYYY-MM-DD" placeholder="不设则留空" />
        </el-form-item>
        <el-form-item label="最早可接">
          <el-date-picker v-model="availFrom" type="date" value-format="YYYY-MM-DD" placeholder="不设则留空" />
        </el-form-item>
        <el-form-item label="黑名单备注">
          <el-input v-model="blacklist" type="textarea" :rows="2" placeholder="留空=非黑名单" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" @click="saveEdit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.page { padding: 16px; }
.toolbar { display: flex; gap: 10px; align-items: center; margin-bottom: 12px; }
.count { color: var(--el-text-color-secondary); font-size: 13px; }
</style>
