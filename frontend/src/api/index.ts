/** Axios 封装：自动带 JWT，401 跳登录。 */
import axios from 'axios'
import { ElMessage } from 'element-plus'

export const api = axios.create({ baseURL: '/api', timeout: 60_000 })

api.interceptors.request.use((cfg) => {
  const t = localStorage.getItem('token')
  if (t) cfg.headers.Authorization = `Bearer ${t}`
  return cfg
})

api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err.response?.status === 401) {
      localStorage.removeItem('token')
      location.hash = '#/login'
    }
    ElMessage.error(err.response?.data?.detail ?? err.message ?? '请求失败')
    return Promise.reject(err)
  },
)

/** 登录 */
export async function login(username: string, password: string) {
  const r = await api.post('/auth/login', { username, password })
  localStorage.setItem('token', r.data.token)
  return r.data
}

/** 新建会话 */
export async function newConversation(title: string) {
  const r = await api.post('/conversations', { title })
  return r.data.conv_id as string
}

/** 会话列表 */
export async function listConversations() {
  const r = await api.get('/conversations')
  return r.data
}

/** 历史消息 */
export async function getMessages(convId: string) {
  const r = await api.get(`/conversations/${convId}/messages`)
  return r.data.messages as Array<{ msg_id: string; role: string; content: string; created_at: string }>
}

/** 商单跟进记录 */
export async function getFollowups(dealId: string) {
  const r = await api.get(`/deals/${dealId}/followups`)
  return r.data.items
}

/** 方案列表 */
export async function listProposals() {
  const r = await api.get('/proposals')
  return r.data.items
}
