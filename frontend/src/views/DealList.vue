<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'

interface Deal {
  deal_id: string; brand_name: string | null; category: string | null
  sub_category: string | null; budget: number | null; goal: string | null
  stage: string | null; demand_desc: string | null
  start_date: string | null; end_date: string | null; created_at: string
}

const items = ref<Deal[]>([])
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    const r = await api.get('/deals', { params: { limit: 100 } })
    items.value = r.data.items
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <el-table :data="items" v-loading="loading" height="calc(100vh - 110px)" size="small">
      <el-table-column prop="deal_id" label="商单号" width="120" fixed />
      <el-table-column prop="brand_name" label="品牌" width="140" />
      <el-table-column prop="category" label="类目" width="90" />
      <el-table-column prop="goal" label="目标" min-width="140" show-overflow-tooltip />
      <el-table-column label="预算" width="110">
        <template #default="{ row }">
          {{ row.budget != null ? '¥' + Number(row.budget).toLocaleString() : '-' }}
        </template>
      </el-table-column>
      <el-table-column prop="stage" label="阶段" width="100" />
      <el-table-column label="投放期" width="200">
        <template #default="{ row }">{{ row.start_date ?? '-' }} ~ {{ row.end_date ?? '-' }}</template>
      </el-table-column>
      <el-table-column prop="created_at" label="创建时间" width="170" />
    </el-table>
  </div>
</template>

<style scoped>
.page { padding: 16px; }
</style>
