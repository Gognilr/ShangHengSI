import { onBeforeUnmount, onMounted } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { dismissToast, toast } from '@/utils/toast'

const UNDO_MS = 5000

interface Pending {
  run: (opts: { keepalive: boolean }) => Promise<unknown>
  onError?: (error: unknown) => void
  timer: ReturnType<typeof setTimeout>
}

/**
 * 延迟删除（V31-U2 1.4、2.2）：schedule 后显示“已删除 · 撤销”，5 秒后才真删。
 * 同时只显示一条提示；新的删除替换提示时，旧条目立即执行（不丢删除）。
 * 离开路由、组件卸载时立即执行全部；关闭标签页时用 keepalive 发出。
 */
export function usePendingDeletes() {
  const pending = new Map<string, Pending>()

  function execute(key: string, keepalive = false) {
    const item = pending.get(key)
    if (!item) return
    pending.delete(key)
    clearTimeout(item.timer)
    item.run({ keepalive }).catch((e) => item.onError?.(e))
  }

  function schedule(key: string, run: Pending['run'], options: { label: string; onUndo: () => void; onError?: (e: unknown) => void }) {
    for (const other of [...pending.keys()]) execute(other)
    const timer = setTimeout(() => execute(key), UNDO_MS)
    pending.set(key, { run, onError: options.onError, timer })
    toast(options.label, {
      duration: UNDO_MS,
      action: {
        label: '撤销',
        onClick: () => {
          const item = pending.get(key)
          if (!item) return
          clearTimeout(item.timer)
          pending.delete(key)
          dismissToast()
          options.onUndo()
        },
      },
    })
  }

  function flush(opts: { keepalive?: boolean } = {}) {
    const hadPending = pending.size > 0
    for (const key of [...pending.keys()]) execute(key, opts.keepalive ?? false)
    if (hadPending) dismissToast()
  }

  const onUnload = () => flush({ keepalive: true })
  onMounted(() => window.addEventListener('beforeunload', onUnload))
  onBeforeUnmount(() => { window.removeEventListener('beforeunload', onUnload); flush() })
  onBeforeRouteLeave(() => { flush() })

  return { schedule, flush, has: (key: string) => pending.has(key) }
}
