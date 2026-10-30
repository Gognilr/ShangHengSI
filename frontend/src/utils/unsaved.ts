import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { ApiError } from '@/api/http'

/** 页面上的一个可保存的节：dirty 与服务端值是否不同；save 失败须抛出；discard 恢复为服务端值。 */
export interface UnsavedSection { dirty: () => boolean; save: () => Promise<void>; discard: () => void }

/** 设置页的未保存修改（V3-A 5）：统一保存 / 放弃，离开页面与关闭标签页时提醒。 */
export function useUnsaved(sections: UnsavedSection[]) {
  const saving = ref(false)
  const dirty = computed(() => sections.some(section => section.dirty()))

  async function saveAll() {
    if (saving.value) return
    saving.value = true
    try {
      for (const section of sections) {
        if (!section.dirty()) continue
        try { await section.save() }
        catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '保存失败，请稍后重试'); return }
      }
      ElMessage.success('已保存')
    } finally { saving.value = false }
  }
  function discardAll() { for (const section of sections) if (section.dirty()) section.discard() }

  onBeforeRouteLeave(async () => {
    if (!dirty.value) return true
    try {
      await ElMessageBox.confirm('有未保存的修改，离开后将丢失。', '未保存的修改', { type: 'warning', confirmButtonText: '离开', cancelButtonText: '留在此页' })
      return true
    } catch { return false }
  })
  const beforeUnload = (event: BeforeUnloadEvent) => { if (dirty.value) event.preventDefault() }
  onMounted(() => window.addEventListener('beforeunload', beforeUnload))
  onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeUnload))

  return { dirty, saving, saveAll, discardAll }
}
