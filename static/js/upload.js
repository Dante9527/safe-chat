/*
 * 檔案上傳 — 拖放與選檔上傳至後端 API（支援批次上傳）
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
  const uploadProgress = ref('')

  /** 拖放事件處理（支援多檔） */
  function onDrop(e) {
    isDragging.value = false
    const files = Array.from(e.dataTransfer.files)
    if (files.length) {
      uploadFiles(files)
    }
  }

  /** 選檔事件處理（支援多檔） */
  function onFileSelect() {
    const f = fileInput.value?.files
    if (f && f.length) {
      uploadFiles(Array.from(f))
    }
  }

  /**
   * 批次上傳：逐一上傳並顯示進度。
   * 先將 FileList 快照為 Array，避免非同步迴圈中被瀏覽器回收。
   * @param {File[]} files - 已轉為 Array 的檔案清單
   */
  async function uploadFiles(files) {
    if (isUploading.value) {
      return
    }
    isUploading.value = true

    const total = files.length
    let succeeded = 0
    let failed = 0

    for (let i = 0; i < total; i++) {
      const file = files[i]
      uploadProgress.value = `(${i + 1}/${total}) ${file.name}`
      try {
        const form = new FormData()
        form.append('file', file)
        const res = await fetchWithAuth('/api/upload', { method: 'POST', body: form })
        const data = await res.json()
        if (!res.ok) {
          throw new Error(data.detail || 'Upload failed')
        }
        succeeded++
        showToast(`(${i + 1}/${total}) ${data.message}`)
      } catch (err) {
        failed++
        showToast(`(${i + 1}/${total}) ${file.name} 失敗：${err.message}`, true)
      }
    }

    if (failed === 0) {
      showToast(`全部完成：${succeeded} 個檔案匯入成功`)
    } else {
      showToast(`完成：${succeeded} 成功、${failed} 失敗`, true)
    }

    await refreshStats()
    uploadProgress.value = ''
    isUploading.value = false
    if (fileInput.value) {
      fileInput.value.value = ''
    }
  }

  return { isUploading, isDragging, fileInput, uploadProgress, onDrop, onFileSelect }
}
