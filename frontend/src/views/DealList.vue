<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api'
import DealDrawer from '../components/DealDrawer.vue'

interface Deal {
  deal_id: string; brand_name: string | null; category: string | null
  sub_category: string | null; budget: number | null; goal: string | null
  stage: string | null; demand_desc: string | null
  start_date: string | null; end_date: string | null; created_at: string
}

const items = ref<Deal[]>([])
const loading = ref(false)
const drawerDealId = ref<string | null>(null)
const stageFilter = ref('')
const q = ref('')
const page = ref(1)
const pageSize = ref(25)

const STAGES = ['需求沟通', '提案', '签约', '执行', '结案', '丢单']
const filtered = ref<Deal[]>([])

// 阶段计数（tabs 徽标）
const stageCounts = computed<Record<string, number>>(() => {
  const m: Record<string, number> = {}
  for (const d of items.value) m[d.stage ?? ''] = (m[d.stage ?? ''] ?? 0) + 1
  return m
})
// 当前筛选口径统计
const stats = computed(() => {
  const totalBudget = filtered.value.reduce((s, d) => s + (d.budget ?? 0), 0)
  const stages = filtered.value.map((d) => d.stage)
  return {
    count: filtered.value.length,
    totalBudget,
    running: stages.filter((s) => s === '执行').length,
    done: stages.filter((s) => s === '结案').length,
  }
})

function applyFilter() {
  const kw = q.value.trim().toLowerCase()
  filtered.value = items.value.filter((d) => {
    if (stageFilter.value && d.stage !== stageFilter.value) return false
    if (kw) {
      const hay = `${d.deal_id} ${d.brand_name ?? ''} ${d.category ?? ''}`.toLowerCase()
      if (!hay.includes(kw)) return false
    }
    return true
  })
  page.value = 1
}
watch([stageFilter], applyFilter)

async function load() {
  loading.value = true
  try {
    const r = await api.get('/deals', { params: { limit: 500 } })
    items.value = r.data.items
    applyFilter()
  } finally {
    loading.value = false
  }
}

const paged = computed(() =>
  filtered.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value))
const pages = computed(() => Math.max(1, Math.ceil(filtered.value.length / pageSize.value)))
function goPage(p: number) { page.value = Math.min(Math.max(1, p), pages.value) }

const STAGE_CLS: Record<string, string> = {
  需求沟通: 's-info', 提案: 's-propose', 签约: 's-sign',
  执行: 's-run', 结案: 's-done', 丢单: 's-lost',
}
function stageCls(s: string | null) { return STAGE_CLS[s ?? ''] ?? 's-info' }
const fmtMoney = (n: number | null) => (n != null ? '¥' + Number(n).toLocaleString() : '-')
const fmtWan = (n: number) => n >= 10000 ? (n / 10000).toFixed(1) + ' 万' : Number(n).toLocaleString()

function openDeal(row: Deal) { drawerDealId.value = row.deal_id }

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="toolbar">
      <div class="title">商单台账 <span class="title-sub" v-if="items.length">{{ items.length }} 单</span></div>
      <div class="controls">
        <input v-model="q" class="search" placeholder="搜商单号 / 品牌 / 类目…" @input="applyFilter" />
      </div>
    </div>

    <!-- 阶段 tabs + 统计条 -->
    <div class="tabs-row">
      <a class="tab" :class="{ on: !stageFilter }" @click="stageFilter = ''">
        全部 <b class="badge">{{ items.length }}</b>
      </a>
      <a v-for="s in STAGES" :key="s" class="tab" :class="{ on: stageFilter === s }" @click="stageFilter = s">
        {{ s }} <b class="badge">{{ stageCounts[s] ?? 0 }}</b>
      </a>
      <div class="stat-inline" v-if="filtered.length">
        <span>在投 <b>{{ stats.running }}</b></span>
        <span>已结案 <b>{{ stats.done }}</b></span>
        <span>预算合计 <b>¥{{ fmtWan(stats.totalBudget) }}</b></span>
      </div>
    </div>

    <div class="table-wrap" v-loading="loading">
      <table class="grid">
        <thead>
          <tr>
            <th style="width:36px"></th>
            <th>商单号</th><th>品牌</th><th>类目</th><th>目标</th>
            <th class="num">预算</th><th>阶段</th><th>投放期</th><th class="num">进度</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!loading && !paged.length">
            <td colspan="9" class="empty-cell">暂无匹配的商单</td>
          </tr>
          <tr v-for="d in paged" :key="d.deal_id" @click="openDeal(d)">
            <td class="idx">{{ (page - 1) * pageSize + paged.indexOf(d) + 1 }}</td>
            <td class="mono">{{ d.deal_id }}</td>
            <td class="brand">{{ d.brand_name ?? '-' }}</td>
            <td>{{ d.category ?? '-' }}<span v-if="d.sub_category" class="sub-cat"> / {{ d.sub_category }}</span></td>
            <td class="goal">{{ d.goal ?? '-' }}</td>
            <td class="num tabular money">{{ fmtMoney(d.budget) }}</td>
            <td><span class="stage" :class="stageCls(d.stage)"><i class="dot" />{{ d.stage ?? '-' }}</span></td>
            <td class="date">{{ d.start_date ?? '-' }} ~ {{ d.end_date ?? '-' }}</td>
            <td class="num"><button class="link" @click.stop="openDeal(d)">详情</button></td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 分页 -->
    <div class="pager" v-if="filtered.length > pageSize">
      <button class="pg" :disabled="page <= 1" @click="goPage(page - 1)">‹ 上一页</button>
      <button v-for="p in Math.min(pages, 7)" :key="p" class="pg" :class="{ on: p === page }" @click="goPage(p)">{{ p }}</button>
      <span class="pg-ellipsis" v-if="pages > 7">…</span>
      <button class="pg" :disabled="page >= pages" @click="goPage(page + 1)">下一页 ›</button>
      <span class="pg-info">{{ filtered.length }} 单 · 每页 {{ pageSize }}</span>
    </div>

    <DealDrawer :deal-id="drawerDealId" @close="drawerDealId = null" @changed="load" />
  </div>
</template>

<style scoped>
.page { padding: 18px 20px; height: 100%; display: flex; flex-direction: column; }
.toolbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; flex-wrap: wrap; gap: 10px; }
.title { font-size: 18px; font-weight: 700; }
.title-sub { font-size: 12.5px; color: var(--ink-3); font-weight: 500; margin-left: 6px; }
.controls { display: flex; gap: 8px; }
.search { font: inherit; font-size: 13px; padding: 7px 11px; border: 1px solid var(--line-strong);
  border-radius: var(--r-sm); background: var(--surface); color: var(--ink); width: 220px; }
.search:focus { outline: none; border-color: var(--brand); }

/* tabs + 内联统计 */
.tabs-row { display: flex; gap: 6px; align-items: center; margin-bottom: 12px; flex-wrap: wrap; }
.tab { display: inline-flex; align-items: center; gap: 5px; padding: 6px 13px; border: 1px solid var(--line-strong);
  border-radius: 20px; background: var(--surface); color: var(--ink-2); font: inherit; font-size: 12.5px;
  cursor: pointer; transition: all 120ms ease-out; }
.tab:hover { border-color: var(--brand); color: var(--brand-strong); }
.tab.on { background: var(--brand); border-color: var(--brand); color: #fff; }
.badge { font-size: 11px; padding: 1px 7px; border-radius: 12px; background: rgba(0,0,0,.08); font-weight: 700; }
.tab.on .badge { background: rgba(255,255,255,.25); }
.stat-inline { margin-left: auto; display: flex; gap: 14px; font-size: 12.5px; color: var(--ink-3); }
.stat-inline b { color: var(--ink); font-variant-numeric: tabular-nums; }

.table-wrap { flex: 1; overflow: auto; background: var(--surface); border: 1px solid var(--line);
  border-radius: var(--r-lg); box-shadow: var(--shadow-1); }
.grid { width: 100%; border-collapse: collapse; font-size: 13px; min-width: 900px; }
.grid thead th { position: sticky; top: 0; z-index: 1; text-align: left; font-weight: 600;
  color: var(--ink-2); font-size: 12px; background: var(--surface-2); padding: 10px 12px;
  border-bottom: 2px solid var(--line-strong); white-space: nowrap; }
.grid tbody td { padding: 9px 12px; border-bottom: 1px solid var(--line); vertical-align: middle; }
.grid tbody tr { cursor: pointer; transition: background 100ms ease-out; }
.grid tbody tr:nth-child(even) { background: #fbfaf7; }
.grid tbody tr:hover { background: var(--brand-softer); }
.idx { color: var(--ink-3); font-size: 11.5px; font-variant-numeric: tabular-nums; }
.mono { font-family: var(--mono); font-size: 12px; color: var(--ink-2); }
.brand { font-weight: 600; }
.sub-cat { color: var(--ink-3); font-size: 12px; }
.goal { max-width: 150px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.num { text-align: right; }
.money { font-weight: 600; }
.date { font-size: 12px; color: var(--ink-2); white-space: nowrap; }
.empty-cell { text-align: center; color: var(--ink-3); padding: 40px 0; }
.link { border: none; background: transparent; color: var(--brand); font: inherit; font-size: 12.5px;
  cursor: pointer; font-weight: 600; padding: 3px 8px; border-radius: 4px; }
.link:hover { background: var(--brand-soft); }

.stage { display: inline-flex; align-items: center; gap: 6px; font-size: 12px;
  padding: 3px 10px; border-radius: 20px; font-weight: 600; white-space: nowrap; }
.stage .dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
.s-info { background: var(--surface-2); color: var(--ink-2); }
.s-propose { background: var(--brand-soft); color: var(--brand-strong); }
.s-sign { background: #e8e6f4; color: #4b4391; }
.s-run { background: var(--warn-soft); color: var(--warn); }
.s-done { background: var(--ok-soft); color: var(--ok); }
.s-lost { background: var(--danger-soft); color: var(--danger); }

.pager { display: flex; align-items: center; gap: 6px; padding: 12px 2px 0; }
.pg { min-width: 30px; padding: 5px 9px; border: 1px solid var(--line-strong); border-radius: var(--r-sm);
  background: var(--surface); color: var(--ink-2); font: inherit; font-size: 12.5px; cursor: pointer; }
.pg:hover:not(:disabled) { border-color: var(--brand); color: var(--brand-strong); }
.pg.on { background: var(--brand); border-color: var(--brand); color: #fff; font-weight: 600; }
.pg:disabled { opacity: .4; cursor: not-allowed; }
.pg-ellipsis { color: var(--ink-3); }
.pg-info { margin-left: auto; font-size: 12px; color: var(--ink-3); }
</style>
