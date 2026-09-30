<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'

interface UserRow {
  user_id: string
  username: string
  display_name: string
  role: string
  created_at: string
}

const rows = ref<UserRow[]>([])
const loading = ref(false)
const me = ref<{ user_id: string; role: string } | null>(null)

// 搜索/筛选
const keyword = ref('')
const roleFilter = ref('')

const showCreate = ref(false)
const form = ref({ username: '', password: '', display_name: '', role: 'viewer' })

const showEdit = ref(false)
const editing = ref<UserRow | null>(null)
const editForm = ref({ display_name: '', role: 'viewer', password: '' })

const ROLE_LABEL: Record<string, string> = { admin: '管理员', operator: '运营', viewer: '只读' }

const filtered = computed(() =>
  rows.value.filter((u) => {
    if (roleFilter.value && u.role !== roleFilter.value) return false
    const kw = keyword.value.trim().toLowerCase()
    if (!kw) return true
    return u.username.toLowerCase().includes(kw) || u.display_name.toLowerCase().includes(kw)
  }),
)

// 统计卡片
const statAdmins = computed(() => rows.value.filter((u) => u.role === 'admin').length)
const statOperators = computed(() => rows.value.filter((u) => u.role === 'operator').length)
const statViewers = computed(() => rows.value.filter((u) => u.role === 'viewer').length)

async function load() {
  loading.value = true
  try {
    me.value = (await api.get('/me')).data
    rows.value = (await api.get('/admin/users')).data.items
  } finally {
    loading.value = false
  }
}

async function create() {
  if (!form.value.username.trim() || form.value.password.length < 6) {
    ElMessage.warning('用户名必填，密码至少 6 位')
    return
  }
  await api.post('/admin/users', form.value)
  ElMessage.success(`已创建 ${form.value.username}`)
  showCreate.value = false
  form.value = { username: '', password: '', display_name: '', role: 'viewer' }
  await load()
}

function openEdit(u: UserRow) {
  editing.value = u
  editForm.value = { display_name: u.display_name, role: u.role, password: '' }
  showEdit.value = true
}

async function saveEdit() {
  const body: Record<string, string> = { display_name: editForm.value.display_name, role: editForm.value.role }
  if (editForm.value.password) {
    if (editForm.value.password.length < 6) { ElMessage.warning('密码至少 6 位'); return }
    body.password = editForm.value.password
  }
  await api.patch(`/admin/users/${editing.value!.user_id}`, body)
  ElMessage.success('已保存')
  showEdit.value = false
  await load()
}

async function remove(u: UserRow) {
  await ElMessageBox.confirm(`确定删除用户「${u.username}」？该操作不可恢复。`, '删除用户', { type: 'warning' })
  await api.delete(`/admin/users/${u.user_id}`)
  ElMessage.success('已删除')
  await load()
}

onMounted(load)
</script>

<template>
  <div class="page">
    <!-- 统计卡片 -->
    <div class="stat-row">
      <div class="stat-card">
        <div class="stat-title">用户总数</div>
        <div class="stat-num green">{{ rows.length }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-title">管理员</div>
        <div class="stat-num blue">{{ statAdmins }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-title">运营</div>
        <div class="stat-num orange">{{ statOperators }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-title">只读</div>
        <div class="stat-num gray">{{ statViewers }}</div>
      </div>
    </div>

    <!-- 表格卡片 -->
    <div class="table-card">
      <div class="toolbar">
        <h2>用户管理</h2>
        <div class="filters">
          <el-input v-model="keyword" placeholder="搜索用户名 / 显示名" clearable class="kw" />
          <el-select v-model="roleFilter" placeholder="全部角色" clearable class="role-sel">
            <el-option label="管理员" value="admin" />
            <el-option label="运营" value="operator" />
            <el-option label="只读" value="viewer" />
          </el-select>
          <el-button type="primary" @click="showCreate = true">＋ 新建用户</el-button>
        </div>
      </div>

      <el-table :data="filtered" v-loading="loading" stripe border>
        <el-table-column prop="username" label="用户名" min-width="140" />
        <el-table-column prop="display_name" label="显示名" min-width="140" />
        <el-table-column label="角色" width="110" align="center">
          <template #default="{ row }">
            <el-tag :type="row.role === 'admin' ? 'danger' : row.role === 'operator' ? 'warning' : 'info'" size="small">
              {{ ROLE_LABEL[row.role] ?? row.role }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" min-width="180">
          <template #default="{ row }">{{ new Date(row.created_at).toLocaleString() }}</template>
        </el-table-column>
        <el-table-column label="操作" width="160" align="center" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" plain @click="openEdit(row)">编辑</el-button>
            <el-button size="small" type="danger" plain :disabled="row.user_id === me?.user_id"
                       @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="没有匹配的用户" />
        </template>
      </el-table>
    </div>

    <el-dialog v-model="showCreate" title="新建用户" width="440">
      <el-form label-width="90">
        <el-form-item label="用户名"><el-input v-model="form.username" /></el-form-item>
        <el-form-item label="密码"><el-input v-model="form.password" type="password" show-password /></el-form-item>
        <el-form-item label="显示名"><el-input v-model="form.display_name" placeholder="默认同用户名" /></el-form-item>
        <el-form-item label="角色">
          <el-select v-model="form.role" class="role-sel">
            <el-option label="只读（viewer）" value="viewer" />
            <el-option label="运营（operator）" value="operator" />
            <el-option label="管理员（admin）" value="admin" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreate = false">取消</el-button>
        <el-button type="primary" @click="create">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showEdit" :title="`编辑 ${editing?.username}`" width="440">
      <el-form label-width="90">
        <el-form-item label="显示名"><el-input v-model="editForm.display_name" /></el-form-item>
        <el-form-item label="角色">
          <el-select v-model="editForm.role" :disabled="editing?.user_id === me?.user_id" class="role-sel">
            <el-option label="只读（viewer）" value="viewer" />
            <el-option label="运营（operator）" value="operator" />
            <el-option label="管理员（admin）" value="admin" />
          </el-select>
          <div v-if="editing?.user_id === me?.user_id" class="hint">不能降级自己的角色</div>
        </el-form-item>
        <el-form-item label="重置密码"><el-input v-model="editForm.password" type="password" show-password placeholder="留空则不修改" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showEdit = false">取消</el-button>
        <el-button type="primary" @click="saveEdit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.page { padding: 18px 22px; }

.stat-row { display: flex; gap: 16px; margin-bottom: 16px; }
.stat-card {
  flex: 1; background: #fff; border: 1px solid #ebeef5; border-radius: 8px;
  padding: 14px 18px; box-shadow: 0 1px 4px rgba(0, 0, 0, .04);
}
.stat-title { font-size: 13px; color: #909399; margin-bottom: 6px; }
.stat-num { font-size: 26px; font-weight: 700; line-height: 1; }
.green { color: #28a745; }
.blue { color: #007bff; }
.orange { color: #ff9800; }
.gray { color: #606266; }

.table-card {
  background: #fff; border: 1px solid #ebeef5; border-radius: 8px;
  padding: 16px 18px; box-shadow: 0 1px 4px rgba(0, 0, 0, .04);
}
.toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }
.toolbar h2 { margin: 0; font-size: 17px; }
.filters { display: flex; gap: 10px; align-items: center; }
.kw { width: 220px; }
.role-sel { width: 170px; }
.hint { font-size: 12px; color: #909399; margin-top: 4px; }
</style>
