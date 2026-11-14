import { defineStore } from 'pinia'
import { ref } from 'vue'

/** 输入框草稿（V321-E-12）：按会话（及讨论锚点）存内存，发送成功才清；草稿是未脱敏原话，不写 localStorage，刷新即清。 */
export const useDraftsStore = defineStore('drafts', () => {
  const items = ref<Record<string, string>>({})
  const get = (key: string) => items.value[key] ?? ''
  function set(key: string, value: string) { if (value) items.value[key] = value; else delete items.value[key] }
  return { get, set }
})
