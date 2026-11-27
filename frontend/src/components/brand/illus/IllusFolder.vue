<script setup lang="ts">
import './illus.css'
// 发票管家 · 票夹：夹子夹着扇形展开的三张票，最上一张左上角有二维码方块；悬停时最上一张向上抽出 6px，
// 拖入时（.open）夹片张开 12°、票扇展开（澄明二版 6.29）；共 15 个元素
</script>

<template>
  <svg viewBox="0 0 220 120" fill="none" class="il il-folder" aria-hidden="true">
  <g class="il-fan-l"><rect x="68" y="26" width="84" height="60" rx="5" class="il-p2" transform="rotate(-11 110 24)"/></g>
  <g class="il-fan-r"><rect x="68" y="26" width="84" height="60" rx="5" class="il-p2" transform="rotate(10 110 24)"/></g>
  <g class="il-pull">
    <rect x="68" y="26" width="84" height="60" rx="5" class="il-p"/>
    <rect x="75" y="34" width="15" height="15" rx="2" class="il-c"/>
    <path d="M79 38h3v3h-3zM84 43h2v2h-2z" class="il-b"/>
    <path d="M96 38h42M96 46h26M76 60h68M76 70h38" class="il-l"/>
    <path d="M122 78h22" class="il-a"/>
  </g>
  <g class="il-jaw">
    <path d="M100 16V9.5a10 4.5 0 0 1 20 0V16" class="il-t"/>
    <rect x="92" y="15" width="36" height="15" rx="3" class="il-c"/>
    <path d="M98 22.5h24" class="il-t"/>
  </g>
</svg>
</template>
