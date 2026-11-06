// V31-U5F-04：Ctrl+V 粘贴文件（资源管理器复制的文件、截图），与拖入走同一流程
import { onBeforeUnmount, onMounted } from 'vue'

function stamp(): string {
  const d = new Date(), p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`
}

/** 剪贴板中的文件；截图没有真实文件名（浏览器给 image.png），改为“截图-时间.png”。 */
export function pastedFiles(event: ClipboardEvent): File[] {
  const list = Array.from(event.clipboardData?.files ?? [])
  return list.map((file, i) => /^image\.\w+$/i.test(file.name) && file.type.startsWith('image/')
    ? new File([file], `截图-${stamp()}${list.length > 1 ? `-${i + 1}` : ''}.${file.name.split('.').pop()}`, { type: file.type, lastModified: file.lastModified })
    : file)
}

/** 页面级粘贴：剪贴板里有文件时交给 handler 并阻止默认粘贴；只有文字时照常粘贴。enabled 为假时不处理。 */
export function usePasteFiles(handler: (files: File[]) => void, enabled: () => boolean = () => true) {
  function onPaste(event: ClipboardEvent) {
    if (event.defaultPrevented || !enabled()) return
    const files = pastedFiles(event)
    if (!files.length) return
    event.preventDefault()
    handler(files)
  }
  onMounted(() => document.addEventListener('paste', onPaste))
  onBeforeUnmount(() => document.removeEventListener('paste', onPaste))
}
