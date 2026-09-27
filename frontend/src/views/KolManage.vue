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
    exclusive_until: exUntil.value, available_from: availFrom.value, blacklist: blacklist.value,
  })
  ElMessage.success('档期已更新')
  dialog.value = false
  await load()
}

function fmt(v: number | null) {
  if (v == null) return '-'
  return v >= 10000 ? (v / 10000).toFixed(1) + 'w' : String(v)
}
const TIER_CLS: Record<string, string> = { 头部: 't-head', 腰部: 't-mid', 尾部: 't-tail' }

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="toolbar">
      <div class="title">达人库</div>
      <div class="controls">
        <input v-model="q" class="search" placeholder="搜索达人昵称" @keyup.enter="load" />
        <select v-model="tier" class="select" @change="load">
          <option value="">全部层级</option>
          <option value="头部">头部</option>
          <option value="腰部">腰部</option>
          <option value="尾部">尾部</option>
        </select>
        <span class="count">共 {{ total }} 位</span>
      </div>
    </div>

    <div class="table-wrap" v-loading="loading">
      <table class="grid">
        <thead>
          <tr>
            <th>达人</th><th>平台</th><th>类目</th><th>层级</th>
            <th class="num">粉丝</th><th class="num">互动率</th><th class="num">报价21-60s</th>
            <th>排他期</th><th>黑名单</th><th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!loading && !kols.length"><td colspan="10" class="empty-cell">没有匹配的达人</td></tr>
          <tr v-for="k in kols" :key="k.kol_id">
            <td class="name">{{ k.kol_name }}</td>
            <td>{{ k.platform }}</td>
            <td>{{ k.category }}</td>
            <td><span class="tier" :class="TIER_CLS[k.tier]">{{ k.tier }}</span></td>
            <td class="num tabular">{{ fmt(k.fans_count) }}</td>
            <td class="num tabular">{{ (k.interact_rate * 100).toFixed(2) }}%</td>
            <td class="num tabular">{{ fmt(k.price_21_60s) }}</td>
            <td><span v-if="k.exclusive_until" class="flag danger">{{ k.exclusive_until }}</span><span v-else class="dash">-</span></td>
            <td><span v-if="k.blacklist" class="flag warn" :title="k.blacklist">有</span><span v-else class="dash">-</span></td>
            <td><button class="link" @click="openEdit(k)">档期</button></td>
          </tr>
        </tbody>
      </table>
    </div>

    <el-dialog v-model="dialog" :title="`档期管理 - ${editing?.kol_name ?? ''}`" width="420px">
      <el-form label-width="90px">
        <el-form-item label="排他期至">
          <el-date-picker v-model="exUntil" type="date" value-format="YYYY-MM-DD" placeholder="不设则留空" style="width:100%" />
        </el-form-item>
        <el-form-item label="最早可接">
          <el-date-picker v-model="availFrom" type="date" value-format="YYYY-MM-DD" placeholder="不设则留空" style="width:100%" />
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
.page { padding: 18px 20px; height: 100%; display: flex; flex-direction: column; }
.toolbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; flex-wrap: wrap; gap: 10px; }
.title { font-size: 18px; font-weight: 700; }
.controls { display: flex; gap: 8px; align-items: center; }
.search, .select { font: inherit; font-size: 13px; padding: 7px 11px; border: 1px solid var(--line-strong);
  border-radius: var(--r-sm); background: var(--surface); color: var(--ink); }
.search { width: 200px; }
.search:focus, .select:focus { outline: none; border-color: var(--brand); }
.count { font-size: 12.5px; color: var(--ink-3); }

.table-wrap { flex: 1; overflow: auto; background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-lg); box-shadow: var(--shadow-1); }
.grid { width: 100%; border-collapse: collapse; font-size: 13px; min-width: 900px; }
.grid thead th { position: sticky; top: 0; z-index: 1; text-align: left; font-weight: 600; color: var(--ink-2);
  font-size: 12px; background: var(--surface-2); padding: 11px 14px; border-bottom: 2px solid var(--line-strong); white-space: nowrap; }
.grid tbody td { padding: 11px 14px; border-bottom: 1px solid var(--line); }
.grid tbody tr { transition: background 100ms ease-out; }
.grid tbody tr:nth-child(even) { background: #fbfaf7; }
.grid tbody tr:hover { background: var(--brand-softer); }
.num { text-align: right; }
.name { font-weight: 600; }
.empty-cell { text-align: center; color: var(--ink-3); padding: 40px 0; }
.dash { color: var(--ink-3); }

.tier { font-size: 12px; padding: 3px 10px; border-radius: 20px; font-weight: 600; }
.t-head { background: #f0e6f6; color: #6b3fa0; }
.t-mid { background: var(--brand-soft); color: var(--brand-strong); }
.t-tail { background: var(--surface-2); color: var(--ink-2); }

.flag { font-size: 11.5px; padding: 3px 8px; border-radius: 4px; font-weight: 600; }
.flag.danger { background: var(--danger-soft); color: var(--danger); }
.flag.warn { background: var(--warn-soft); color: var(--warn); }
.link { border: none; background: transparent; color: var(--brand); font: inherit; font-size: 12.5px;
  cursor: pointer; font-weight: 600; padding: 3px 6px; border-radius: 4px; }
.link:hover { background: var(--brand-soft); }
</style>
