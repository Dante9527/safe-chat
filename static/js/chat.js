/*
 * 對話邏輯 — 提問送出與 SSE 串流接收
 */

window.SafeChat = window.SafeChat || {}

/**
 * 建立對話提問與串流接收邏輯。
 * @param {object} deps - Vue 工具與外部函式
 */
window.SafeChat.useChat = function useChat({
  ref, reactive, nextTick,
  showToast, scrollBottom, refreshHealth, fetchWithAuth,
  chatContainer, questionInput,
}) {
  const messages = reactive([])
  const question = ref('')
  const isLoading = ref(false)
  let msgId = 0

  /** 處理單一 SSE 事件（token / sources / error） */
  function handleSSEEvent(event, data, botMsg) {
    if (event === 'token') {
      if (isLoading.value) isLoading.value = false
      botMsg.text += data.token
      scrollBottom(chatContainer)
    } else if (event === 'sources') {
      botMsg.sources = data.sources
      botMsg.degraded = data.degraded
    } else if (event === 'error') {
      if (isLoading.value) isLoading.value = false
      botMsg.text = data.fallback_answer
      botMsg.sources = data.sources
      botMsg.degraded = data.degraded
      if (data.degraded) {
        showToast('AI 服務離線 — 改為顯示法規原文', true)
        refreshHealth()
      }
    }
  }

  /** 讀取 SSE 串流，逐行解析並分派事件 */
  async function readSSEStream(response, botMsg) {
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let currentEvent = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop()

      for (const line of lines) {
        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7).trim()
        } else if (line.startsWith('data: ')) {
          handleSSEEvent(currentEvent, JSON.parse(line.slice(6)), botMsg)
        }
      }
    }
  }

  /** 送出提問，透過 SSE 串流接收回答 */
  async function sendQuestion() {
    const q = question.value.trim()
    if (!q || isLoading.value) return

    messages.push({ id: ++msgId, role: 'user', text: q, sources: null })
    question.value = ''
    if (questionInput.value) questionInput.value.style.height = 'auto'

    messages.push({ id: ++msgId, role: 'bot', text: '', sources: null, degraded: false })
    const botMsg = messages[messages.length - 1]
    isLoading.value = true
    await scrollBottom(chatContainer)

    try {
      const res = await fetchWithAuth('/api/ask/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: q }),
      })
      await readSSEStream(res, botMsg)
    } catch (err) {
      botMsg.text = `發生錯誤：${err.message}`
    } finally {
      isLoading.value = false
      await scrollBottom(chatContainer)
      nextTick(() => questionInput.value?.focus())
    }
  }

  /** 快速提問 */
  function askQuick(q) {
    question.value = q
    sendQuestion()
  }

  return { messages, question, isLoading, sendQuestion, askQuick }
}
