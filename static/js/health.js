/*
 * 健康狀態 — 每 30 秒輪詢 /api/health，驅動燈號顯示
 */

window.SafeChat = window.SafeChat || {}

/**
 * 建立健康狀態輪詢與燈號計算邏輯。
 * @param {object} deps - Vue 工具（reactive, computed）
 */
window.SafeChat.useHealth = function useHealth({ reactive, computed, fetchWithAuth }) {
  const health = reactive({ status: 'ok', components: {} })
  let healthTimer = null

  /** 向 /api/health 請求最新狀態 */
  async function refreshHealth() {
    try {
      const res = await fetchWithAuth('/api/health')
      const data = await res.json()
      health.status = data.status
      health.components = data.components
    } catch {
      health.status = 'unreachable'
      health.components = {}
    }
  }

  /** 燈號 CSS class（綠 / 黃 / 紅） */
  const healthDotClass = computed(() => {
    if (health.status === 'ok') {
      return ''
    }
    if (health.status === 'degraded') {
      return 'warn'
    }
    return 'error'
  })

  /** 燈號旁顯示的文字標籤 */
  const healthLabel = computed(() => {
    if (health.status === 'ok') {
      return 'RAG Engine'
    }
    if (health.status === 'degraded') {
      const c = health.components || {}
      if (c.llm && !c.llm.ok) {
        return 'AI 離線'
      }
      if (c.vector && !c.vector.ok) {
        return '向量庫異常'
      }
      if (c.disk && !c.disk.ok) {
        return '磁碟空間不足'
      }
      return '降級模式'
    }
    return '連線中斷'
  })

  /** 滑鼠懸停時的詳細狀態提示 */
  const healthTooltip = computed(() => {
    const c = health.components || {}
    const lines = []
    if (c.llm) {
      lines.push(
        `LLM (${c.llm.backend}/${c.llm.model}): ${c.llm.ok ? '✓' : '✗ ' + (c.llm.error || '')}`,
      )
    }
    if (c.vector) {
      lines.push(
        `Vector store: ${c.vector.ok ? '✓ ' + c.vector.chunks + ' chunks' : '✗'}`,
      )
    }
    if (c.disk) {
      lines.push(
        `Disk: ${c.disk.ok ? '✓ ' + c.disk.free_mb + ' MB free' : '✗ low space'}`,
      )
    }
    return lines.join('\n') || '狀態未知'
  })

  /** 啟動定期輪詢（每 30 秒） */
  function startPolling() {
    refreshHealth()
    healthTimer = setInterval(refreshHealth, 30000)
  }

  /** 停止定期輪詢 */
  function stopPolling() {
    if (healthTimer) {
      clearInterval(healthTimer)
    }
  }

  return {
    health,
    healthDotClass,
    healthLabel,
    healthTooltip,
    refreshHealth,
    startPolling,
    stopPolling,
  }
}
