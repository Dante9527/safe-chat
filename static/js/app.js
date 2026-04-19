/*
 * SafeChat — Vue 3 應用入口
 * 組合各 composable 模組，掛載至 #app。
 */

const { createApp, ref, reactive, computed, nextTick, onMounted, onUnmounted } = Vue
const { useHelpers, useStats, useUpload, useChat, useHealth } = window.SafeChat

createApp({
  setup() {
    // Template refs
    const chatContainer = ref(null)
    const questionInput = ref(null)

    // 側邊欄快速提問範例
    const quickQuestions = [
      '施工架搭設有哪些安全規範？',
      '工地發生墜落事故的通報流程為何？',
      '開挖作業前需要做哪些安全檢查？',
      '電氣作業的防護措施有哪些？',
      '高溫作業的危害預防措施？',
    ]

    // 組合各模組
    const helpers = useHelpers({ reactive, nextTick })
    const stats = useStats({ ref, reactive, fetchWithAuth: helpers.fetchWithAuth })
    const health = useHealth({
      reactive,
      computed,
      fetchWithAuth: helpers.fetchWithAuth,
    })

    const upload = useUpload({
      ref,
      showToast: helpers.showToast,
      refreshStats: stats.refreshStats,
      fetchWithAuth: helpers.fetchWithAuth,
    })

    const chat = useChat({
      ref,
      reactive,
      nextTick,
      showToast: helpers.showToast,
      scrollBottom: helpers.scrollBottom,
      refreshHealth: health.refreshHealth,
      fetchWithAuth: helpers.fetchWithAuth,
      chatContainer,
      questionInput,
    })

    // 生命週期
    onMounted(() => {
      stats.refreshStats()
      health.startPolling()
      questionInput.value?.focus()
    })

    onUnmounted(() => {
      health.stopPolling()
    })

    // 清空知識庫（需管理員 token）
    async function resetKB() {
      if (!confirm('確定要清空所有已匯入的文件？')) {
        return
      }
      const token = prompt('請輸入管理員 token：')
      if (!token) {
        return
      }
      try {
        const res = await helpers.fetchWithAuth('/api/reset', {
          method: 'DELETE',
          headers: { 'X-Admin-Token': token },
        })
        if (!res.ok) {
          const data = await res.json().catch(() => ({}))
          throw new Error(data.detail || `HTTP ${res.status}`)
        }
        helpers.showToast('知識庫已清空')
        await stats.refreshStats()
      } catch (err) {
        helpers.showToast(`清空失敗：${err.message}`, true)
      }
    }

    return {
      // Template refs
      chatContainer,
      questionInput,
      // 快速提問
      quickQuestions,
      // Helpers
      toasts: helpers.toasts,
      renderMarkdown: helpers.renderMarkdown,
      autoResize: helpers.autoResize,
      // Stats
      totalChunks: stats.totalChunks,
      documents: stats.documents,
      // Upload
      isUploading: upload.isUploading,
      isDragging: upload.isDragging,
      fileInput: upload.fileInput,
      uploadProgress: upload.uploadProgress,
      onDrop: upload.onDrop,
      onFileSelect: upload.onFileSelect,
      // Chat
      messages: chat.messages,
      question: chat.question,
      isLoading: chat.isLoading,
      sendQuestion: chat.sendQuestion,
      askQuick: chat.askQuick,
      // Health
      health: health.health,
      healthDotClass: health.healthDotClass,
      healthLabel: health.healthLabel,
      healthTooltip: health.healthTooltip,
      // Reset
      resetKB,
    }
  },
}).mount('#app')
