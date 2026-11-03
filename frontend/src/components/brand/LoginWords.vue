<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useFx, useLoop, watchFrames } from '@/utils/motion'
const canvas = ref<HTMLCanvasElement>(), fx = useFx(), running = useLoop(ref(true))
const letters = '合同履行责任义务期限付款交付保密争议权利约定通知商务审核'
let frame = 0, resize: ResizeObserver | undefined, stopFrames = () => {}, color = '', family = '', width = 0, height = 0
let dots: { x: number; y: number; size: number; alpha: number; char: string }[] = []
function draw(time = 0) {
  const ctx = canvas.value?.getContext('2d'); if (!ctx) return
  ctx.clearRect(0,0,width,height)
  if (fx.value === 'off') return
  for (const dot of dots) { ctx.globalAlpha = dot.alpha; ctx.fillStyle = color; ctx.font = `${dot.size}px ${family}`; ctx.fillText(dot.char,dot.x, (dot.y - time * 0.006 % height + height) % height) }
  ctx.globalAlpha = 1
}
function setup() {
  if (!canvas.value) return
  width = canvas.value.clientWidth; height = canvas.value.clientHeight
  const dpr = window.devicePixelRatio || 1; canvas.value.width = width*dpr; canvas.value.height = height*dpr
  canvas.value.getContext('2d')?.scale(dpr,dpr)
  family = getComputedStyle(canvas.value).fontFamily
  color = getComputedStyle(canvas.value).getPropertyValue('--si-ink-4').trim()
  // R2 低差异序列：均匀散开，不排成斜线（固定步长取模会连成一行行“乱码”）
  dots = Array.from({ length: Math.floor(width*height/5200) }, (_,i) => ({x: (0.5+i*0.7548776662)%1*width,y: (0.5+i*0.5698402910)%1*height,size:12+i*5%7,alpha:0.07+(i*4%9)*0.02,char:letters[i*7%letters.length]!}))
  draw()
}
function animate(now: number) { draw(now); if (running.value) frame = requestAnimationFrame(animate) }
watch(running, value => { cancelAnimationFrame(frame); stopFrames(); if (value) { stopFrames = watchFrames(); frame = requestAnimationFrame(animate) } else draw() })
watch(fx, setup)
onMounted(() => { setup(); resize = new ResizeObserver(setup); resize.observe(canvas.value!); if (running.value) { stopFrames = watchFrames(); frame = requestAnimationFrame(animate) } })
onBeforeUnmount(() => { cancelAnimationFrame(frame); stopFrames(); resize?.disconnect() })
</script>
<template><canvas ref="canvas" aria-hidden="true" /></template>
<style scoped>canvas { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }</style>
