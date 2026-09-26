/**
 * SSE 对话客户端：POST /api/chat/{convId}，解析 event/data 帧。
 * fetch 流式读取（EventSource 不支持 POST + Authorization）。
 */
export interface ChatEvent {
  type: 'token' | 'tool_call' | 'tool_result' | 'done' | 'error'
  text?: string
  name?: string
  args?: Record<string, unknown>
  preview?: string
  error?: string
  node?: string
}

export async function streamChat(
  convId: string,
  text: string,
  onEvent: (ev: ChatEvent) => void,
): Promise<void> {
  const resp = await fetch(`/api/chat/${convId}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${localStorage.getItem('token') ?? ''}`,
    },
    body: JSON.stringify({ text }),
  })
  if (!resp.ok || !resp.body) {
    let detail = `HTTP ${resp.status}`
    try {
      const j = await resp.json()
      detail = j.detail ?? detail
    } catch { /* ignore */ }
    onEvent({ type: 'error', error: detail })
    return
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
          onEvent({ type: ev as ChatEvent['type'], ...JSON.parse(data) })
        } catch { /* 忽略残帧 */ }
      }
    }
  }
  onEvent({ type: 'done' })
}
