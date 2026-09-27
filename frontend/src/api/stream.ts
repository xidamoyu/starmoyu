/**
 * SSE 对话客户端：POST /api/chat/{convId}，解析 event/data 帧。
 * fetch 流式读取（EventSource 不支持 POST + Authorization）。
 */
export interface ChatEvent {
  type: 'token' | 'tool_call' | 'tool_result' | 'done' | 'error' | 'deal_context'
  text?: string
  name?: string
  args?: Record<string, unknown>
  preview?: string
  error?: string
  node?: string
  /** deal_context 事件：钉住状态 / 新检测到的商单号 */
  pinned_deal_id?: string | null
  detected_deal_id?: string | null
}

export interface StreamResult {
  /** 本轮对话过程中钉住的商单（来自 deal_context 事件） */
  pinnedDealId: string | null
  /** 检测到的、尚未关联的商单号（用于弹「关联」提示） */
  detected: string[]
}

export async function streamChat(
  convId: string,
  text: string,
  onEvent: (ev: ChatEvent) => void,
  dealId: string | null = null,
): Promise<StreamResult> {
  const result: StreamResult = { pinnedDealId: null, detected: [] }
  const resp = await fetch(`/api/chat/${convId}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${localStorage.getItem('token') ?? ''}`,
    },
    body: JSON.stringify({ text, deal_id: dealId }),
  })
  if (!resp.ok || !resp.body) {
    let detail = `HTTP ${resp.status}`
    try {
      const j = await resp.json()
      detail = j.detail ?? detail
    } catch { /* ignore */ }
    onEvent({ type: 'error', error: detail })
    return result
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    // SSE 帧以空行分隔
    const frames = buf.split('\n\n')
    buf = frames.pop() ?? ''
    for (const frame of frames) {
      let ev = ''
      let data = ''
      for (const line of frame.split('\n')) {
        if (line.startsWith('event: ')) ev = line.slice(7).trim()
        else if (line.startsWith('data: ')) data += line.slice(6)
      }
      if (ev && data) {
        try {
          const parsed = JSON.parse(data)
          if (ev === 'deal_context') {
            if (parsed.pinned_deal_id) result.pinnedDealId = parsed.pinned_deal_id
            if (parsed.detected_deal_id && !result.detected.includes(parsed.detected_deal_id)) {
              result.detected.push(parsed.detected_deal_id)
            }
          }
          onEvent({ type: ev as ChatEvent['type'], ...parsed })
        } catch { /* 忽略残帧 */ }
      }
    }
  }
  onEvent({ type: 'done' })
  return result
}
