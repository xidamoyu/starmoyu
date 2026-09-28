<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { ElMessage } from 'element-plus'
import Md from '../components/Md.vue'

interface Proposal {
  proposal_id: string; requirement_text: string; status: string
  content: string; created_at: string; updated_at: string
}
interface DealChange {
  request_id: string; deal_id: string; field_name: string
  old_value: string; new_value: string; change_summary: string
  status: string; created_by: string; reviewed_by: string; created_at: string
}

const tab = ref<'proposal' | 'deal'>('proposal')
const STATUS: Record<string, { label: string; cls: string }> = {
  draft: { label: '草稿', cls: 'st-draft' },
  pending_review: { label: '待审批', cls: 'st-pending' },
  pending: { label: '待审批', cls: 'st-pending' },
  approved: { label: '已通过', cls: 'st-approved' },
  rejected: { label: '已驳回', cls: 'st-rejected' },
}
const FIELD_LABEL: Record<string, string> = {
  budget: '预算', owner: '负责人', stage: '阶段', demand_desc: '需求描述',
  goal: '投放目标', cpm_target: 'CPM目标', platform_req: '平台要求',
}

const items = ref<Proposal[]>([])
const changes = ref<DealChange[]>([])
const loading = ref(false)
const drawerVisible = ref(false)
const detail = ref<Proposal | null>(null)
const versions = ref<{ version_no: number; content: string; comment: string; changed_by: string }[]>([])

async function load() {
  loading.value = true
  try {
    const [p, d] = await Promise.all([
      api.get('/proposals'), api.get('/deal-changes'),
    ])
    items.value = p.data.items
    changes.value = d.data.items
  } finally {
    loading.value = false
  }
}

async function openDetail(p: Proposal) {
  const r = await api.get(`/proposals/${p.proposal_id}`)
  detail.value = r.data
  versions.value = r.data.versions ?? []
  drawerVisible.value = true
}

async function review(p: Proposal, action: string) {
  const comment = action === 'approve' ? '通过' : '需修改后重提'
  await api.post(`/proposals/${p.proposal_id}/review`, { action, comment })
  ElMessage.success(action === 'approve' ? '已通过' : '已驳回')
  detail.value = null
  drawerVisible.value = false
  await load()
}

async function reviewChange(c: DealChange, action: 'approve' | 'reject') {
  const url = action === 'approve'
    ? `/deal-changes/${c.request_id}/approve` : `/deal-changes/${c.request_id}/reject`
  await api.post(url, { comment: action === 'approve' ? '通过' : '维持原值' })
  ElMessage.success(action === 'approve' ? '已批准, 商单已更新' : '已驳回')
  await load()
}

const stCls = (s: string) => STATUS[s]?.cls ?? 'st-draft'
const stLabel = (s: string) => STATUS[s]?.label ?? s
const fldLabel = (f: string) => FIELD_LABEL[f] ?? f

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="toolbar">
      <div class="title">审批中心</div>
      <div class="tabs">
        <button class="tab" :class="{ on: tab === 'proposal' }" @click="tab = 'proposal'">方案审批</button>
        <button class="tab" :class="{ on: tab === 'deal' }" @click="tab = 'deal'">
          商单变更<span v-if="changes.filter(c => c.status === 'pending').length" class="badge">{{ changes.filter(c => c.status === 'pending').length }}</span>
        </button>
      </div>
    </div>

    <!-- 方案审批 -->
    <div v-show="tab === 'proposal'" class="table-wrap" v-loading="loading">
      <table class="grid">
        <thead>
          <tr><th>方案号</th><th>需求</th><th>状态</th><th>更新时间</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-if="!loading && !items.length"><td colspan="5" class="empty-cell">暂无方案</td></tr>
          <tr v-for="p in items" :key="p.proposal_id">
            <td class="mono">{{ p.proposal_id }}</td>
            <td class="req">{{ p.requirement_text }}</td>
            <td><span class="st" :class="stCls(p.status)"><i class="dot" />{{ stLabel(p.status) }}</span></td>
            <td class="date">{{ (p.updated_at || '').replace('T', ' ').slice(0, 16) }}</td>
            <td class="ops">
              <button class="link" @click="openDetail(p)">查看</button>
              <template v-if="p.status === 'pending_review'">
                <button class="link ok" @click="review(p, 'approve')">通过</button>
                <button class="link no" @click="review(p, 'reject')">驳回</button>
              </template>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 商单变更审批 -->
    <div v-show="tab === 'deal'" class="table-wrap" v-loading="loading">
      <table class="grid">
        <thead>
          <tr><th>申请号</th><th>商单</th><th>字段</th><th>原值 → 新值</th><th>说明</th><th>状态</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-if="!loading && !changes.length"><td colspan="7" class="empty-cell">暂无变更申请</td></tr>
          <tr v-for="c in changes" :key="c.request_id">
            <td class="mono">{{ c.request_id.slice(-8) }}</td>
            <td class="mono">{{ c.deal_id }}</td>
            <td class="fld">{{ fldLabel(c.field_name) }}</td>
            <td class="chg"><span class="old">{{ c.old_value || '—' }}</span> → <span class="new">{{ c.new_value }}</span></td>
            <td class="req">{{ c.change_summary || '—' }}</td>
            <td><span class="st" :class="stCls(c.status)"><i class="dot" />{{ stLabel(c.status) }}</span></td>
            <td class="ops">
              <template v-if="c.status === 'pending'">
                <button class="link ok" @click="reviewChange(c, 'approve')">批准</button>
                <button class="link no" @click="reviewChange(c, 'reject')">驳回</button>
              </template>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <el-drawer v-model="drawerVisible" size="56%" :with-header="false" class="prop-drawer">
      <div v-if="detail" class="detail">
        <div class="d-head">
          <div class="d-id">{{ detail.proposal_id }}</div>
          <span class="st" :class="stCls(detail.status)"><i class="dot" />{{ stLabel(detail.status) }}</span>
        </div>
        <div class="d-req">{{ detail.requirement_text }}</div>

        <div class="d-sec-title">方案正文</div>
        <div class="d-content"><Md :text="detail.content || '_（空）_'" /></div>

        <div class="d-sec-title">版本历史（{{ versions.length }}）</div>
        <div v-for="v in versions" :key="v.version_no" class="ver">
          <b>v{{ v.version_no }}</b> · {{ v.changed_by ?? '-' }} · {{ v.comment }}
        </div>

        <div v-if="detail.status === 'pending_review'" class="d-actions">
          <button class="btn ghost" @click="drawerVisible = false">关闭</button>
          <button class="btn danger" @click="review(detail, 'reject')">驳回</button>
          <button class="btn primary" @click="review(detail, 'approve')">通过</button>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.page { padding: 18px 20px; height: 100%; display: flex; flex-direction: column; }
.toolbar { margin-bottom: 14px; display: flex; align-items: center; gap: 18px; }
.title { font-size: 18px; font-weight: 700; }
.tabs { display: flex; gap: 4px; background: var(--surface-2); padding: 3px; border-radius: var(--r-md); }
.tab { border: none; background: transparent; padding: 6px 14px; border-radius: var(--r-sm); font: inherit; font-size: 13px; font-weight: 600; color: var(--ink-2); cursor: pointer; display: inline-flex; align-items: center; gap: 6px; }
.tab.on { background: var(--surface); color: var(--brand); box-shadow: var(--shadow-1); }
.badge { background: var(--danger); color: #fff; font-size: 10px; min-width: 16px; height: 16px; border-radius: 8px; display: inline-flex; align-items: center; justify-content: center; padding: 0 4px; }

.table-wrap { flex: 1; overflow: auto; background: var(--surface); border: 1px solid var(--line); border-radius: var(--r-lg); box-shadow: var(--shadow-1); }
.grid { width: 100%; border-collapse: collapse; font-size: 13px; min-width: 720px; }
.grid thead th { position: sticky; top: 0; z-index: 1; text-align: left; font-weight: 600; color: var(--ink-2);
  font-size: 12px; background: var(--surface-2); padding: 11px 14px; border-bottom: 2px solid var(--line-strong); white-space: nowrap; }
.grid tbody td { padding: 11px 14px; border-bottom: 1px solid var(--line); }
.grid tbody tr { transition: background 100ms ease-out; }
.grid tbody tr:nth-child(even) { background: #fbfaf7; }
.grid tbody tr:hover { background: var(--brand-softer); }
.mono { font-family: var(--mono); font-size: 12px; color: var(--ink-2); }
.req { max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--ink-2); }
.fld { font-weight: 600; white-space: nowrap; }
.chg { font-size: 12.5px; white-space: nowrap; }
.chg .old { color: var(--ink-3); text-decoration: line-through; }
.chg .new { color: var(--brand); font-weight: 600; }
.date { font-size: 12px; color: var(--ink-2); white-space: nowrap; }
.empty-cell { text-align: center; color: var(--ink-3); padding: 40px 0; }
.ops { white-space: nowrap; }

.st { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; padding: 3px 10px;
  border-radius: 20px; font-weight: 600; }
.st .dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
.st-draft { background: var(--surface-2); color: var(--ink-2); }
.st-pending { background: var(--warn-soft); color: var(--warn); }
.st-approved { background: var(--ok-soft); color: var(--ok); }
.st-rejected { background: var(--danger-soft); color: var(--danger); }

.link { border: none; background: transparent; color: var(--brand); font: inherit; font-size: 12.5px;
  cursor: pointer; font-weight: 600; padding: 3px 6px; border-radius: 4px; }
.link:hover { background: var(--brand-soft); }
.link.ok { color: var(--ok); } .link.ok:hover { background: var(--ok-soft); }
.link.no { color: var(--danger); } .link.no:hover { background: var(--danger-soft); }

.detail { padding: 4px 2px; }
.d-head { display: flex; align-items: center; gap: 12px; }
.d-id { font-family: var(--mono); font-size: 14px; font-weight: 700; }
.d-req { font-size: 14px; color: var(--ink-2); margin: 8px 0 16px; padding-bottom: 12px; border-bottom: 1px solid var(--line); }
.d-sec-title { font-size: 12px; font-weight: 700; color: var(--ink-3); margin: 16px 0 8px; }
.d-content { background: var(--paper); padding: 14px 16px; border-radius: var(--r-md); border: 1px solid var(--line); }
.ver { font-size: 12px; color: var(--ink-2); padding: 6px 0; border-bottom: 1px dashed var(--line); }
.ver:last-child { border-bottom: none; }
.d-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 20px; }
.btn { padding: 8px 16px; border-radius: var(--r-sm); font: inherit; font-weight: 600; cursor: pointer; border: 1px solid var(--line-strong); }
.btn.primary { background: var(--brand); color: #fff; border-color: var(--brand); }
.btn.danger { background: var(--danger); color: #fff; border-color: var(--danger); }
.btn.ghost { background: var(--surface); color: var(--ink-2); }
</style>
