<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { ElMessage } from 'element-plus'

interface Proposal {
  proposal_id: string; requirement_text: string; status: string
  content: string; created_at: string; updated_at: string
}

const STATUS: Record<string, { label: string; type: 'info' | 'warning' | 'success' | 'danger' }> = {
  draft: { label: '草稿', type: 'info' },
  pending_review: { label: '待审批', type: 'warning' },
  approved: { label: '已通过', type: 'success' },
  rejected: { label: '已驳回', type: 'danger' },
}

const items = ref<Proposal[]>([])
const loading = ref(false)
const drawerVisible = ref(false)
const detail = ref<Proposal | null>(null)
const versions = ref<{ version_no: number; content: string; comment: string; changed_by: string }[]>([])

async function load() {
  loading.value = true
  try {
    const r = await api.get('/proposals')
    items.value = r.data.items
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
  await load()
}

onMounted(load)
</script>

<template>
  <div class="page">
    <el-table :data="items" v-loading="loading" height="calc(100vh - 110px)" size="small">
      <el-table-column prop="proposal_id" label="方案号" width="120" />
      <el-table-column prop="requirement_text" label="需求" min-width="220" show-overflow-tooltip />
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="STATUS[row.status]?.type ?? 'info'" size="small">
            {{ STATUS[row.status]?.label ?? row.status }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="updated_at" label="更新时间" width="170" />
      <el-table-column label="操作" width="140" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="openDetail(row)">查看</el-button>
          <template v-if="row.status === 'pending_review'">
            <el-button link type="success" size="small" @click="review(row, 'approve')">通过</el-button>
            <el-button link type="danger" size="small" @click="review(row, 'reject')">驳回</el-button>
          </template>
        </template>
      </el-table-column>
    </el-table>

    <el-drawer v-model="drawerVisible" :title="`方案 ${detail?.proposal_id ?? ''}`" size="50%">
      <div v-if="detail" class="detail">
        <el-descriptions :column="2" size="small" border>
          <el-descriptions-item label="状态">
            <el-tag :type="STATUS[detail.status]?.type ?? 'info'" size="small">
              {{ STATUS[detail.status]?.label ?? detail.status }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="更新时间">{{ detail.updated_at }}</el-descriptions-item>
          <el-descriptions-item label="需求" :span="2">{{ detail.requirement_text }}</el-descriptions-item>
        </el-descriptions>

        <h4>方案正文</h4>
        <div class="content pre-wrap">{{ detail.content }}</div>

        <h4>版本历史（{{ versions.length }}）</h4>
        <div v-for="v in versions" :key="v.version_no" class="ver">
          <b>v{{ v.version_no }}</b> · {{ v.changed_by ?? '-' }} · {{ v.comment }}
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.page { padding: 16px; }
.content { background: #fff; padding: 14px; border-radius: 6px; border: 1px solid var(--el-border-color-light); }
.pre-wrap { white-space: pre-wrap; line-height: 1.7; font-size: 13px; }
.ver { font-size: 12px; color: var(--el-text-color-secondary); padding: 4px 0; border-bottom: 1px dashed var(--el-border-color-lighter); }
</style>
