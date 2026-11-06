import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'

/** 外观设置（V31-U1 3.3）：只存本浏览器，不发送到服务器。 */
export type Shell = 'night' | 'light'
export type FxChoice = 'auto' | 'full' | 'lite' | 'off'
export type FxLevel = Exclude<FxChoice, 'auto'>

const STORAGE_KEY = 'si.appearance'
const SHELLS: Shell[] = ['night', 'light']
const CHOICES: FxChoice[] = ['auto', 'full', 'lite', 'off']

function readStored(): { shell: Shell; fx: FxChoice } {
  try {
    const value = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}') as { shell?: unknown; fx?: unknown }
    return {
      shell: SHELLS.includes(value.shell as Shell) ? value.shell as Shell : 'light',
      fx: CHOICES.includes(value.fx as FxChoice) ? value.fx as FxChoice : 'auto',
    }
  } catch {
    return { shell: 'light', fx: 'auto' }
  }
}

/** WebGL 拿不到“非软件渲染”的上下文，或渲染器为已知的软件实现时，视为软件渲染（远程桌面、无显卡）。 */
function isSoftwareRendering(): boolean {
  try {
    const canvas = document.createElement('canvas')
    const gl = canvas.getContext('webgl', { failIfMajorPerformanceCaveat: true })
    if (!gl) return true
    const info = gl.getExtension('WEBGL_debug_renderer_info')
    const renderer = info ? String(gl.getParameter(info.UNMASKED_RENDERER_WEBGL)) : ''
    gl.getExtension('WEBGL_lose_context')?.loseContext()
    return /SwiftShader|llvmpipe|Basic Render|WARP/i.test(renderer)
  } catch {
    return false
  }
}

export const useAppearanceStore = defineStore('appearance', () => {
  const stored = readStored()
  const shell = ref<Shell>(stored.shell)
  const fxChoice = ref<FxChoice>(stored.fx)
  const reducedMotion = ref(false)
  // 自动档的设备判定只做一次；运行中掉帧只在本次打开期间生效
  const deviceLevel = ref<{ level: FxLevel; reason: string }>({ level: 'full', reason: '' })
  const droppedFrames = ref(false)

  const auto = computed<{ level: FxLevel; reason: string }>(() => {
    if (reducedMotion.value) return { level: 'off', reason: '系统设置了减少动态效果' }
    if (deviceLevel.value.level !== 'full') return deviceLevel.value
    if (droppedFrames.value) return { level: 'lite', reason: '运行中出现明显掉帧' }
    return { level: 'full', reason: '' }
  })
  const fxResolved = computed<FxLevel>(() => fxChoice.value === 'auto' ? auto.value.level : fxChoice.value)
  const fxReason = computed(() => fxChoice.value === 'auto' ? auto.value.reason : '')

  function init(): void {
    const media = matchMedia('(prefers-reduced-motion: reduce)')
    reducedMotion.value = media.matches
    media.addEventListener('change', e => { reducedMotion.value = e.matches })
    if (isSoftwareRendering()) deviceLevel.value = { level: 'lite', reason: '检测到软件渲染（远程桌面或无显卡）' }
    else if ((navigator.hardwareConcurrency ?? 4) <= 2) deviceLevel.value = { level: 'lite', reason: '处理器核心较少' }
  }

  function reportDroppedFrames(): void { droppedFrames.value = true }

  watch([shell, fxResolved], ([s, fx]) => {
    document.documentElement.dataset.shell = s
    document.documentElement.dataset.fx = fx
  }, { immediate: true })
  watch([shell, fxChoice], ([s, fx]) => {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify({ shell: s, fx })) } catch { /* 只影响下次打开 */ }
  })

  return { shell, fxChoice, fxResolved, fxReason, init, reportDroppedFrames }
})
