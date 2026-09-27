<script setup lang="ts">
import { onMounted, ref } from 'vue'
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

const STAGES = ['需求沟通', '提案', '签约', '执行', '结案', '丢单']
const filtered = ref<Deal[]>([])
function applyFilter() {
  filtered.value = stageFilter.value
    ? items.value.filter((d) => d.stage === stageFilter.value)
    : items.value
}

async function load() {
  loading.value = true
  try {
    const r = await api.get('/deals', { params: { limit: 200 } })
    items.value = r.data.items
    applyFilter()
  } finally {
    loading.value = false
  }
}

const STAGE_CLS: Record<string, string> = {
  需求沟通: 's-info', 提案: 's-propose', 签约: 's-sign',
  执行: 's-run', 结案: 's-done', 丢单: 's-lost',
}
function stageCls(s: string | null) { return STAGE_CLS[s ?? ''] ?? 's-info' }
const fmtMoney = (n: number | null) => (n != null ? '¥' + Number(n).toLocaleString() : '-')

function openDeal(row: Deal) { drawerDealId.value = row.deal_id }

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="toolbar">
      <div class="title">商单台账</div>
      <div class="filters">
        <a class="chip-btn" :class="{ on: !stageFilter }" @click="stageFilter = ''; applyFilter()">全部</a>
        <a v-for="s in STAGES" :key="s" class="chip-btn"
           :class="{ on: stageFilter === s }" @click="stageFilter = s; applyFilter()">{{ s }}</a>
      </div>
    </div>

    <div class="table-wrap" v-loading="loading">
      <table class="grid">
        <thead>
          <tr>
            <th>商单号</th><th>品牌</th><th>类目</th><th>目标</th>
            <th class="num">预算</th><th>阶段</th><th>投放期</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!loading && !filtered.length">
            <td colspan="7" class="empty-cell">暂无该阶段的商单</td>
          </tr>
          <tr v-for="d in filtered" :key="d.deal_id" @click="openDeal(d)">
            <td class="mono">{{ d.deal_id }}</td>
            <td>{{ d.brand_name ?? '-' }}</td>
            <td>{{ d.category ?? '-' }}</td>
            <td class="goal">{{ d.goal ?? '-' }}</td>
            <td class="num tabular">{{ fmtMoney(d.budget) }}</td>
            <td><span class="stage" :class="stageCls(d.stage)"><i class="dot" />{{ d.stage ?? '-' }}</span></td>
            <td class="date">{{ d.start_date ?? '-' }} ~ {{ d.end_date ?? '-' }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <DealDrawer :deal-id="drawerDealId" @close="drawerDealId = null" @changed="load" />
  </div>
</template>

<style scoped>
.page { padding: 18px 20px; height: 100%; display: flex; flex-direction: column; }
.toolbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; flex-wrap: wrap; gap: 10px; }
.title { font-size: 18px; font-weight: 700; }
.filters { display: flex; gap: 6px; flex-wrap: wrap; }
.chip-btn { display: inline-block; padding: 5px 12px; border: 1px solid var(--line-strong); border-radius: 20px;
  background: var(--surface); color: var(--ink-2); font: inherit; font-size: 12.5px; cursor: pointer;
  text-decoration: none; transition: background 120ms ease-out, color 120ms ease-out, border-color 120ms ease-out; }
.chip-btn:hover { border-color: var(--brand); color: var(--brand-strong); }
.chip-btn.on { background: var(--brand); border-color: var(--brand); color: #fff; }

.table-wrap { flex: 1; overflow: auto; background: var(--surface); border: 1px solid var(--line);
  border-radius: var(--r-lg); box-shadow: var(--shadow-1); }
.grid { width: 100%; border-collapse: collapse; font-size: 13px; min-width: 860px; }
.grid thead th { position: sticky; top: 0; z-index: 1; text-align: left; font-weight: 600;
  color: var(--ink-2); font-size: 12px; background: var(--surface-2); padding: 11px 14px;
  border-bottom: 2px solid var(--line-strong); white-space: nowrap; }
.grid tbody td { padding: 11px 14px; border-bottom: 1px solid var(--line); vertical-align: middle; }
.grid tbody tr { cursor: pointer; transition: background 100ms ease-out; }
.grid tbody tr:nth-child(even) { background: #fbfaf7; }
.grid tbody tr:hover { background: var(--brand-softer); }
.mono { font-family: var(--mono); font-size: 12px; color: var(--ink-2); }
.goal { max-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.num { text-align: right; }
.date { font-size: 12px; color: var(--ink-2); white-space: nowrap; }
.empty-cell { text-align: center; color: var(--ink-3); padding: 40px 0; }

.stage { display: inline-flex; align-items: center; gap: 6px; font-size: 12px;
  padding: 3px 10px; border-radius: 20px; font-weight: 600; white-space: nowrap; }
.stage .dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
.s-info { background: var(--surface-2); color: var(--ink-2); }
.s-propose { background: var(--brand-soft); color: var(--brand-strong); }
.s-sign { background: #e8e6f4; color: #4b4391; }
.s-run { background: var(--warn-soft); color: var(--warn); }
.s-done { background: var(--ok-soft); color: var(--ok); }
.s-lost { background: var(--danger-soft); color: var(--danger); }
</style>
