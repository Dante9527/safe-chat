/*
 * 知識庫統計 — 查詢文件數與 chunk 數
 */

window.SafeChat = window.SafeChat || {}

/**
 * 建立知識庫統計查詢邏輯。
 * @param {object} deps - Vue 工具（ref, reactive）
 */
window.SafeChat.useStats = function useStats({ ref, reactive, fetchWithAuth }) {
  const totalChunks = ref(0)
  const documents = reactive([])

  /** 從 /api/stats 取得最新統計 */
  async function refreshStats() {
    try {
      const res = await fetchWithAuth('/api/stats')
      const data = await res.json()
      totalChunks.value = data.total_chunks
      documents.splice(0, documents.length, ...data.documents)
    } catch (_e) {
      /* stats refresh is non-critical */
    }
  }

  return { totalChunks, documents, refreshStats }
}
