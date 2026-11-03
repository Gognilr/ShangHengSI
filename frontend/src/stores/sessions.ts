import { defineStore } from 'pinia'
import { ref } from 'vue'
import { assistantApi, type SessionItem } from '@/api/assistant'

/** 侧栏“近期会话”：本人会话按最后活动排序的前几页；智能助手页在新建、改名、删除、发送后调用 refresh()。 */
export const useSessionsStore = defineStore('sessions', () => {
  const items = ref<SessionItem[]>([])
  const total = ref(0)
  const page = ref(1)
  const loading = ref(false)
  // 首页（还没有会话）据此决定是否收录音、列“会议纪要”
  const asrEnabled = ref(false)
  let version = 0

  /** 重新取第 1 页到当前已加载的页数。 */
  async function refresh(): Promise<void> {
    const current = ++version, pages = page.value
    loading.value = true
    try {
      const results = []
      // 侧栏只列智能助手的对话；会议纪要、翻译在各自页面列出
      for (let p = 1; p <= pages; p++) results.push(await assistantApi.list(p, '', 'chat'))
      if (current !== version) return
      // 空会话（没有文件、标题仍是“新会话”）不列出：V31-U5 起不再产生，旧数据也不打扰
      items.value = results.flatMap(result => result.items).filter(item => item.file_count > 0 || item.title !== '新会话')
      asrEnabled.value = !!results[0]?.asr_enabled
      total.value = results[results.length - 1]?.total ?? 0
    } catch { /* 侧栏列表失败不打断页面，下次刷新再取 */ }
    finally { if (current === version) loading.value = false }
  }

  async function loadMore(): Promise<void> {
    if (loading.value || items.value.length >= total.value) return
    const current = ++version, next = page.value + 1
    loading.value = true
    try {
      const result = await assistantApi.list(next, '', 'chat')
      if (current !== version) return
      const seen = new Set(items.value.map(item => item.id))
      items.value = [...items.value, ...result.items.filter(item => !seen.has(item.id))]
      total.value = result.total; page.value = next
    } catch { /* 同上 */ }
    finally { if (current === version) loading.value = false }
  }

  function clear(): void { ++version; items.value = []; total.value = 0; page.value = 1; loading.value = false }

  return { items, total, loading, asrEnabled, refresh, loadMore, clear }
})
