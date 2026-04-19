/*
 * 工具函式 — Toast 通知、Markdown 渲染、自動捲動、輸入框自適應高度
 */

window.SafeChat = window.SafeChat || {}

/**
 * 建立通用工具函式集合。
 * @param {object} deps - Vue 響應式工具（reactive, nextTick）
 */
window.SafeChat.useHelpers = function useHelpers({ reactive, nextTick }) {
  const toasts = reactive([])
  let toastId = 0

  /** 右上角顯示 Toast 通知，3.5 秒後自動消失 */
  function showToast(message, isError = false) {
    const id = ++toastId
    toasts.push({ id, message, isError })
    setTimeout(() => {
      const idx = toasts.findIndex((t) => t.id === id)
      if (idx !== -1) {
        toasts.splice(idx, 1)
      }
    }, 3500)
  }

  /** 簡易 Markdown → HTML（粗體、行內碼、換行） */
  function renderMarkdown(text) {
    if (!text) {
      return ''
    }
    return text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(
        /`(.+?)`/g,
        '<code style="background:var(--bg-input);padding:0.1em 0.35em;border-radius:4px;font-family:var(--font-mono);font-size:0.85em;">$1</code>',
      )
      .replace(/\n/g, '<br>')
  }

  /** 對話區自動捲到底部 */
  async function scrollBottom(chatContainer) {
    await nextTick()
    if (chatContainer.value) {
      chatContainer.value.scrollTop = chatContainer.value.scrollHeight
    }
  }

  /** 輸入框自動調整高度 */
  function autoResize(e) {
    const el = e.target
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 120) + 'px'
  }

  /**
   * 帶 API Key 的 fetch 包裝。
   * 若 sessionStorage 中有 safechat_api_key，自動附加 Authorization 標頭。
   */
  function fetchWithAuth(url, options = {}) {
    const apiKey = sessionStorage.getItem('safechat_api_key')
    if (apiKey) {
      const headers = new Headers(options.headers || {})
      headers.set('Authorization', `Bearer ${apiKey}`)
      return fetch(url, { ...options, headers })
    }
    return fetch(url, options)
  }

  return { toasts, showToast, renderMarkdown, scrollBottom, autoResize, fetchWithAuth }
}
