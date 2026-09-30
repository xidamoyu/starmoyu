<script setup lang="ts">
import { onMounted, ref } from 'vue'
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

const showCreate = ref(false)
const form = ref({ username: '', password: '', display_name: '', role: 'viewer' })

const showEdit = ref(false)
const editing = ref<UserRow | null>(null)
const editForm = ref({ display_name: '', role: 'viewer', password: '' })

async function load() {
  loading.value = true
  try {
    const meR = await api.get('/me')
    me.value = meR.data
    const r = await api.get('/admin/users')
    rows.value = r.data.items
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
  form.value = { username: '', password: '', display_name: '', role: 'user' }
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
    <div class="head">
      <h2>用户管理</h2>
      <el-button type="primary" @click="showCreate = true">新建用户</el-button>
    </div>

    <el-table :data="rows" v-loading="loading" stripe>
      <el-table-column prop="username" label="用户名" min-width="120" />
      <el-table-column prop="display_name" label="显示名" min-width="120" />
      <el-table-column label="角色" width="100">
        <template #default="{ row }">
          <el-tag :type="row.role === 'admin' ? 'danger' : row.role === 'operator' ? 'warning' : 'info'" size="small">
            {{ {admin: '管理员', operator: '运营', viewer: '只读'}[row.role] ?? row.role }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="创建时间" min-width="160">
        <template #default="{ row }">{{ new Date(row.created_at).toLocaleString() }}</template>
      </el-table-column>
      <el-table-column label="操作" width="150" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click="openEdit(row)">编辑</el-button>
          <el-button size="small" type="danger" :disabled="row.user_id === me?.user_id"
                     @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="showCreate" title="新建用户" width="420">
      <el-form label-width="80">
        <el-form-item label="用户名"><el-input v-model="form.username" /></el-form-item>
        <el-form-item label="密码"><el-input v-model="form.password" type="password" show-password /></el-form-item>
        <el-form-item label="显示名"><el-input v-model="form.display_name" placeholder="默认同用户名" /></el-form-item>
        <el-form-item label="角色">
          <el-select v-model="form.role">
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

    <el-dialog v-model="showEdit" :title="`编辑 ${editing?.username}`" width="420">
      <el-form label-width="80">
        <el-form-item label="显示名"><el-input v-model="editForm.display_name" /></el-form-item>
        <el-form-item label="角色">
          <el-select v-model="editForm.role" :disabled="editing?.user_id === me?.user_id">
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
.page { padding: 20px 24px; }
.head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }
.head h2 { margin: 0; font-size: 18px; }
.hint { font-size: 12px; color: var(--el-text-color-secondary); margin-top: 4px; }
</style>
