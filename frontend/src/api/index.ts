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

/** 登出：清本地 token 并回登录页 */
export function logout() {
  localStorage.removeItem('token')
  location.hash = '#/login'
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

/** M6: 商单钉住 / 取消钉住 */
export async function pinDeal(convId: string, dealId: string | null) {
  const r = await api.patch(`/conversations/${convId}/pin`, { deal_id: dealId })
  return r.data as { conv_id: string; pinned_deal_id: string | null }
}

/** M6: 商单当前钉住 */
export async function getPinned(convId: string) {
  const r = await api.get(`/conversations/${convId}/pin`)
  return r.data as { conv_id: string; pinned_deal_id: string | null }
}

/** M6: 商单时间线（stage + 跟进流水 + 特质溯源） */
export interface Timeline {
  deal_id: string; brand_name: string; category: string; goal: string
  budget: number; stage: string; demand_desc: string; kol_ids: string[]
  start_date: string; end_date: string; result_metrics: Record<string, unknown> | null
  stages: string[]
  followups: Array<{ id: number; stage_from: string; stage_to: string; note: string
    operator: string; created_at: string; action_type: string | null }>
  traits: Array<{ trait_id: string; party_type: string; party_id: string
    trait_category: string; trait_content: string; source_quote: string
    severity: string; verified: boolean; created_at: string }>
}
export async function dealTimeline(dealId: string) {
  const r = await api.get(`/deals/${dealId}/timeline`)
  return r.data as Timeline
}

/** M6: 修改商单阶段 */
export async function setDealStage(dealId: string, stage: string, note = '') {
  const r = await api.post(`/deals/${dealId}/stage`, { stage, note })
  return r.data as { deal_id: string; stage_from: string; stage_to: string; followup_id: string }
}

/** M6: 跟进备注 */
export async function addFollowup(dealId: string, note: string) {
  const r = await api.post(`/deals/${dealId}/followups`, { note })
  return r.data
}

/** M6: 结案表单直调（UI 确定性路径） */
export interface CloseDealPayload {
  roi?: number | null; gmv?: number | null; exposure?: number | null
  interaction?: number | null; cpm?: number | null; views?: number | null
  summary_note?: string
  traits?: Array<{ kol_id: string; trait_category: string; trait_content: string
    source_quote: string; severity: string }>
}
export async function closeDeal(dealId: string, payload: CloseDealPayload) {
  const r = await api.post(`/deals/${dealId}/close`, payload)
  return r.data as { ok: boolean; deal_result: Record<string, unknown>; trait_ids: string[] }
}

/** 沉淀流：出案例预览（不入库） */
export async function sedimentPreview(dealId: string, extraLessons: string[] = []) {
  const r = await api.post(`/deals/${dealId}/sediment/preview`, { extra_lessons: extraLessons })
  return r.data as { deal_id: string; rel_path: string; title: string; char_len: number; preview: string }
}
/** 沉淀流：确认入库（增量嵌入回流知识库） */
export async function sedimentConfirm(dealId: string, extraLessons: string[] = []) {
  const r = await api.post(`/deals/${dealId}/sediment/confirm`, { extra_lessons: extraLessons })
  return r.data as { ok: boolean; deal_id: string; sediment: { chunks: number; replaced_old: number } }
}

/** 视觉抽取能力是否可用（后端探测；当前置灰） */
export async function visionEnabled() {
  try {
    const r = await api.get('/vision/enabled')
    return Boolean((r.data as { enabled?: boolean }).enabled)
  } catch {
    return false
  }
}
