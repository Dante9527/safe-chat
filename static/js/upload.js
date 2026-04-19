/*
 * 檔案上傳 — 拖放與選檔上傳至後端 API
 */

window.SafeChat = window.SafeChat || {}

/**
 * 建立檔案上傳相關邏輯。
 * @param {object} deps - Vue ref、showToast、refreshStats
 */
window.SafeChat.useUpload = function useUpload({
  ref,
  showToast,
  refreshStats,
  fetchWithAuth,
}) {
  const isUploading = ref(false)
  const isDragging = ref(false)
  const fileInput = ref(null)

  /** 拖放事件處理 */
  function onDrop(e) {
    isDragging.value = false
    if (e.dataTransfer.files.length) {
      uploadFile(e.dataTransfer.files[0])
    }
  }

  /** 選檔事件處理 */
  function onFileSelect() {
    const f = fileInput.value?.files
    if (f && f.length) {
      uploadFile(f[0])
    }
  }

  /** 上傳單一檔案至 /api/upload */
  async function uploadFile(file) {
    isUploading.value = true
    showToast(`正在匯入 ${file.name}…`)
    const form = new FormData()
    form.append('file', file)

    try {
      const res = await fetchWithAuth('/api/upload', { method: 'POST', body: form })
      const data = await res.json()
      if (!res.ok) {
        throw new Error(data.detail || 'Upload failed')
      }
      showToast(data.message)
      await refreshStats()
    } catch (err) {
      showToast(`上傳失敗：${err.message}`, true)
    } finally {
      isUploading.value = false
      if (fileInput.value) {
        fileInput.value.value = ''
      }
    }
  }

  return { isUploading, isDragging, fileInput, onDrop, onFileSelect }
}
