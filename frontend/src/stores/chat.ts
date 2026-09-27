/** 对话状态管理。 */
import { defineStore } from 'pinia'
import { listConversations, newConversation, pinDeal } from '../api'
import { streamChat, type ChatEvent } from '../api/stream'

export interface ToolStep {
  kind: 'tool_call' | 'tool_result'
  name: string
  detail: string
}

export interface ChatMsg {
  role: 'user' | 'assistant'
  text: string
  steps?: ToolStep[]   // assistant 消息附带的工具调用过程
  error?: boolean
}

export const useChatStore = defineStore('chat', {
  state: () => ({
    convId: '' as string,
    convList: [] as Array<{ conv_id: string; title: string; last_active_at: string }>,
    messages: [] as ChatMsg[],
    streaming: false,
    /** M6: 本会话钉住的商单（对话内沉淀自动溯源） */
    pinnedDealId: null as string | null,
    /** M6: 检测到、尚未关联的商单号（用于「关联」提示条） */
    detectedDealIds: [] as string[],
  }),
  actions: {
    async refreshConversations() {
      const r = await listConversations()
      this.convList = (r ?? []).map((x: { row: Record<string, string> }) => ({
        conv_id: x.row.conv_id, title: x.row.title ?? '新对话',
        last_active_at: x.row.last_active_at ?? '',
      }))
    },
    async startNew() {
      this.convId = await newConversation('新对话')
      this.messages = []
      this.pinnedDealId = null
      this.detectedDealIds = []
      await this.refreshConversations()
    },
    select(convId: string) {
      this.convId = convId
      this.messages = []
      this.detectedDealIds = []
    },
    /** M6: 关联 / 取消关联商单到本会话 */
    async setPinned(dealId: string | null) {
      if (!this.convId) return
      const r = await pinDeal(this.convId, dealId)
      this.pinnedDealId = r.pinned_deal_id
      if (dealId) this.detectedDealIds = this.detectedDealIds.filter((d) => d !== dealId)
    },
    /** M6: 忽略某条检测提示 */
    dismissDetected(dealId: string) {
      this.detectedDealIds = this.detectedDealIds.filter((d) => d !== dealId)
    },
    async send(text: string) {
      if (!text.trim() || this.streaming) return
      if (!this.convId) await this.startNew()
      this.messages.push({ role: 'user', text })
      this.streaming = true

      const assistantMsg: ChatMsg = { role: 'assistant', text: '', steps: [] }
      this.messages.push(assistantMsg)

      const onEvent = (ev: ChatEvent) => {
        if (ev.type === 'token') {
          assistantMsg.text += ev.text ?? ''
        } else if (ev.type === 'tool_call') {
          assistantMsg.steps!.push({
            kind: 'tool_call', name: ev.name ?? '?',
            detail: JSON.stringify(ev.args ?? {}, null, 0).slice(0, 160),
          })
        } else if (ev.type === 'tool_result') {
          assistantMsg.steps!.push({
            kind: 'tool_result', name: ev.name ?? '?',
            detail: (ev.preview ?? '').slice(0, 160),
          })
        } else if (ev.type === 'error') {
          assistantMsg.error = true
          assistantMsg.text += `\n\n⚠️ ${ev.error ?? '未知错误'}`
        }
        // 触发响应式（push 后再改字段需要整体替换）
        this.messages = [...this.messages]
      }

      try {
        const res = await streamChat(this.convId, text, onEvent, this.pinnedDealId)
        // M6: 同步钉住状态与检测到的商单
        if (res.pinnedDealId) this.pinnedDealId = res.pinnedDealId
        for (const d of res.detected) {
          if (d !== this.pinnedDealId && !this.detectedDealIds.includes(d)) {
            this.detectedDealIds.push(d)
          }
        }
      } finally {
        this.streaming = false
        await this.refreshConversations()
      }
    },
  },
})
