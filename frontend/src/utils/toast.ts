import { shallowRef } from 'vue'

/** 可撤销提示（V31-U1 第五节）：同时只显示一条，新的替换旧的。不带操作的短反馈仍用 ElMessage。 */
export interface ToastOptions {
  action?: { label: string; onClick: () => void }
  duration?: number
}

export interface ToastState extends ToastOptions {
  id: number
  text: string
  duration: number
}

export const currentToast = shallowRef<ToastState | null>(null)

let seq = 0

export function toast(text: string, options: ToastOptions = {}): void {
  seq += 1
  currentToast.value = { id: seq, text, action: options.action, duration: options.duration ?? 5000 }
}

export function dismissToast(): void {
  currentToast.value = null
}
