<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api'
import { ElMessage } from 'element-plus'

interface Kol {
  kol_id: string; kol_name: string; platform: string; category: string
  sub_category: string; tier: string; fans_count: number
  interact_rate: number; price_21_60s: number; avg_views: number
  exclusive_until: string | null; available_from: string | null; blacklist: string | null
}
interface Stats { sum_fans: number; avg_price: number; exclusive_count: number; blacklist_count: number }
interface Facet { category: string; count: number }

const kols = ref<Kol[]>([])
const stats = ref<Stats | null>(null)
const facets = ref<Facet[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const q = ref('')
const tier = ref('')
const category = ref('')
const sortKey = ref<'fans' | 'price' | 'interact'>('fans')
const loading = ref(false)

const dialog = ref(false)
const editing = ref<Kol | null>(null)
const exUntil = ref('')
const availFrom = ref('')
const blacklist = ref('')

async function load() {
  loading.value = true
  try {
    const r = await api.get('/kols', {
      params: { q: q.value, tier: tier.value, category: category.value,
                page: page.value, page_size: pageSize.value, sort: sortKey.value },
    })
    kols.value = r.data.items
    total.value = r.data.total
    stats.value = r.data.stats
    facets.value = r.data.facets ?? []
  } finally {
    loading.value = false
  }
}
watch([tier, category, sortKey], () => { page.value = 1; load() })
function search() { page.value = 1; load() }
function goPage(p: number) {
  const last = Math.max(1, Math.ceil(total.value / pageSize.value))
  page.value = Math.min(Math.max(1, p), last)
  load()
}
const pages = computed(() => {
  const n = Math.ceil(total.value / pageSize.value)
  // 最多 7 个页码按钮，滑窗
  const cur = page.value
  const start = Math.max(1, Math.min(cur - 3, n - 6))
  return Array.from({ length: Math.min(7, n) }, (_, i) => start + i)
})

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
function fmtFans(v: number | null) {
  if (v == null) return '-'
  if (v >= 100000000) return (v / 100000000).toFixed(2) + '亿'
  if (v >= 10000) return (v / 10000).toFixed(1) + 'w'
  return String(v)
}
const TIER_CLS: Record<string, string> = { 头部: 't-head', 腰部: 't-mid', 尾部: 't-tail', koc: 't-koc' }

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="toolbar">
      <div class="title">达人库 <span v-if="total" class="title-sub">{{ total }} 位达人</span></div>
      <div class="controls">
        <input v-model="q" class="search" placeholder="搜索达人昵称…" @keyup.enter="search" />
        <select v-model="category" class="select" v-if="facets.length">
          <option value="">全部类目</option>
          <option v-for="f in facets" :key="f.category" :value="f.category">
            {{ f.category }}（{{ f.count }}）
          </option>
        </select>
        <select v-model="tier" class="select">
          <option value="">全部层级</option>
          <option value="头部">头部</option>
          <option value="腰部">腰部</option>
          <option value="尾部">尾部</option>
          <option value="koc">KOC</option>
        </select>
        <select v-model="sortKey" class="select">
          <option value="fans">按粉丝量</option>
          <option value="price">按报价</option>
          <option value="interact">按互动率</option>
        </select>
        <button class="btn" @click="search">查询</button>
      </div>
    </div>

    <!-- 统计条 -->
    <div v-if="stats" class="stat-bar">
      <div class="stat"><span class="v">{{ total }}</span><span class="k">达人总数</span></div>
      <div class="stat"><span class="v">{{ fmtFans(stats.sum_fans) }}</span><span class="k">粉丝总量</span></div>
      <div class="stat"><span class="v">¥{{ stats.avg_price.toLocaleString() }}</span><span class="k">平均报价</span></div>
      <div class="stat warn"><span class="v">{{ stats.exclusive_count }}</span><span class="k">排期占用</span></div>
      <div class="stat danger"><span class="v">{{ stats.blacklist_count }}</span><span class="k">黑名单</span></div>
    </div>

    <div class="table-wrap" v-loading="loading">
      <table class="grid">
        <thead>
          <tr>
            <th style="width:36px"></th>
            <th>达人</th><th>平台</th><th>类目</th><th>层级</th>
            <th class="num">粉丝</th><th class="num">互动率</th><th class="num">均播</th>
            <th class="num">报价21-60s</th>
            <th>排他期</th><th>最早可接</th><th>黑名单</th><th style="width:60px"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!loading && !kols.length"><td colspan="13" class="empty-cell">没有匹配的达人</td></tr>
          <tr v-for="k in kols" :key="k.kol_id">
            <td class="idx">{{ (page - 1) * pageSize + kols.indexOf(k) + 1 }}</td>
            <td>
              <div class="name">{{ k.kol_name }}</div>
              <div class="sub-id">{{ k.kol_id }}</div>
            </td>
            <td>{{ k.platform }}</td>
            <td>{{ k.category }}<span v-if="k.sub_category" class="sub-cat"> / {{ k.sub_category }}</span></td>
            <td><span class="tier" :class="TIER_CLS[k.tier]">{{ k.tier }}</span></td>
            <td class="num tabular">{{ fmtFans(k.fans_count) }}</td>
            <td class="num tabular">{{ (k.interact_rate * 100).toFixed(2) }}%</td>
            <td class="num tabular">{{ fmt(k.avg_views) }}</td>
            <td class="num tabular price">¥{{ Number(k.price_21_60s).toLocaleString() }}</td>
            <td><span v-if="k.exclusive_until" class="flag danger">至 {{ k.exclusive_until }}</span><span v-else class="dash">可接</span></td>
            <td><span v-if="k.available_from" class="flag ok">{{ k.available_from }} 起</span><span v-else class="dash">-</span></td>
            <td><span v-if="k.blacklist" class="flag warn" :title="k.blacklist">有</span><span v-else class="dash">-</span></td>
            <td><button class="link" @click="openEdit(k)">档期</button></td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 分页 -->
    <div class="pager" v-if="total > pageSize">
      <button class="pg" :disabled="page <= 1" @click="goPage(page - 1)">‹ 上一页</button>
      <button v-for="p in pages" :key="p" class="pg" :class="{ on: p === page }" @click="goPage(p)">{{ p }}</button>
      <span class="pg-ellipsis" v-if="Math.ceil(total / pageSize) > 7">…</span>
      <button class="pg" :disabled="page >= Math.ceil(total / pageSize)" @click="goPage(page + 1)">下一页 ›</button>
      <span class="pg-info">{{ total }} 条 · 每页 {{ pageSize }}</span>
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
.toolbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; flex-wrap: wrap; gap: 10px; }
.title { font-size: 18px; font-weight: 700; }
.title-sub { font-size: 12.5px; color: var(--ink-3); font-weight: 500; margin-left: 6px; }
.controls { display: flex; gap: 8px; align-items: center; }
.search, .select { font: inherit; font-size: 13px; padding: 7px 11px; border: 1px solid var(--line-strong);
  border-radius: var(--r-sm); background: var(--surface); color: var(--ink); }
.search { width: 190px; }
.search:focus, .select:focus { outline: none; border-color: var(--brand); }
.btn { font: inherit; font-size: 13px; padding: 7px 16px; border: none; border-radius: var(--r-sm);
  background: var(--brand); color: #fff; font-weight: 600; cursor: pointer; }
.btn:hover { background: var(--brand-strong); }

/* 统计条 */
.stat-bar { display: flex; gap: 10px; margin-bottom: 12px; }
.stat { flex: 1; display: flex; flex-direction: column; gap: 2px; padding: 10px 14px;
  background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-md); }
.stat .v { font-size: 17px; font-weight: 700; font-variant-numeric: tabular-nums; }
.stat .k { font-size: 11.5px; color: var(--ink-3); }
.stat.warn .v { color: var(--warn); }
.stat.danger .v { color: var(--danger); }

.table-wrap { flex: 1; overflow: auto; background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-lg); box-shadow: var(--shadow-1); }
.grid { width: 100%; border-collapse: collapse; font-size: 13px; min-width: 1040px; }
.grid thead th { position: sticky; top: 0; z-index: 1; text-align: left; font-weight: 600; color: var(--ink-2);
  font-size: 12px; background: var(--surface-2); padding: 10px 12px; border-bottom: 2px solid var(--line-strong); white-space: nowrap; }
.grid tbody td { padding: 9px 12px; border-bottom: 1px solid var(--line); }
.grid tbody tr { transition: background 100ms ease-out; }
.grid tbody tr:nth-child(even) { background: #fbfaf7; }
.grid tbody tr:hover { background: var(--brand-softer); }
.num { text-align: right; }
.idx { color: var(--ink-3); font-size: 11.5px; font-variant-numeric: tabular-nums; }
.name { font-weight: 600; }
.sub-id { font-size: 11px; color: var(--ink-3); font-family: var(--mono); }
.sub-cat { color: var(--ink-3); font-size: 12px; }
.price { font-weight: 600; }
.empty-cell { text-align: center; color: var(--ink-3); padding: 40px 0; }
.dash { color: var(--ink-3); font-size: 12px; }

.tier { font-size: 12px; padding: 3px 10px; border-radius: 20px; font-weight: 600; }
.t-head { background: #f0e6f6; color: #6b3fa0; }
.t-mid { background: var(--brand-soft); color: var(--brand-strong); }
.t-tail { background: var(--surface-2); color: var(--ink-2); }
.t-koc { background: #fdf3e3; color: #a3660a; }

.flag { font-size: 11.5px; padding: 3px 8px; border-radius: 4px; font-weight: 600; white-space: nowrap; }
.flag.danger { background: var(--danger-soft); color: var(--danger); }
.flag.warn { background: var(--warn-soft); color: var(--warn); }
.flag.ok { background: var(--ok-soft); color: var(--ok); }
.link { border: none; background: transparent; color: var(--brand); font: inherit; font-size: 12.5px;
  cursor: pointer; font-weight: 600; padding: 3px 6px; border-radius: 4px; }
.link:hover { background: var(--brand-soft); }

/* 分页 */
.pager { display: flex; align-items: center; gap: 6px; padding: 12px 2px 0; }
.pg { min-width: 30px; padding: 5px 9px; border: 1px solid var(--line-strong); border-radius: var(--r-sm);
  background: var(--surface); color: var(--ink-2); font: inherit; font-size: 12.5px; cursor: pointer; }
.pg:hover:not(:disabled) { border-color: var(--brand); color: var(--brand-strong); }
.pg.on { background: var(--brand); border-color: var(--brand); color: #fff; font-weight: 600; }
.pg:disabled { opacity: .4; cursor: not-allowed; }
.pg-ellipsis { color: var(--ink-3); }
.pg-info { margin-left: auto; font-size: 12px; color: var(--ink-3); }
</style>
