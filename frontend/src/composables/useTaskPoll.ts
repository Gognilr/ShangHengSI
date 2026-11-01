import { onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue'
import { ApiError } from '@/api/http'
import { tasksApi, type TaskDetail } from '@/api/tasks'

const POLL_MS = 3000

/**
 * 任务轮询（V31-U2 1.4）：active 为真时每 3 秒读一次任务；同一时刻只有一个请求，
 * 页面隐藏时暂停、恢复可见时立即读一次。任务不存在（404）时停止并置 notFound。
 */
export function useTaskPoll(taskId: Ref<string>, options: { active: Ref<boolean> }) {
  const task = ref<TaskDetail | null>(null)
  const notFound = ref(false)
  let timer: ReturnType<typeof setTimeout> | null = null
  let inflight: Promise<TaskDetail | null> | null = null

  async function refresh(): Promise<TaskDetail | null> {
    const id = taskId.value
    if (!id) return null
    if (inflight) return inflight
    inflight = (async () => {
      try {
        const next = await tasksApi.get(id)
        if (taskId.value !== id) return null
        task.value = next
        notFound.value = false
        return next
      } catch (e) {
        if (e instanceof ApiError && e.status === 404 && taskId.value === id) {
          notFound.value = true
          task.value = null
        }
        return null // 网络中断等其他错误等下一次轮询
      } finally {
        inflight = null
      }
    })()
    return inflight
  }

  function schedule() {
    stop()
    if (!options.active.value || notFound.value || document.hidden || !taskId.value) return
    timer = setTimeout(async () => {
      await refresh()
      schedule()
    }, POLL_MS)
  }

  function stop() {
    if (timer) clearTimeout(timer)
    timer = null
  }

  const onVisibility = () => {
    if (document.hidden) stop()
    else if (options.active.value) void refresh().then(schedule)
  }

  watch([taskId, options.active], () => schedule())
  watch(taskId, () => { task.value = null; notFound.value = false })
  onMounted(() => { document.addEventListener('visibilitychange', onVisibility); schedule() })
  onBeforeUnmount(() => { stop(); document.removeEventListener('visibilitychange', onVisibility) })

  return { task, notFound, refresh }
}
