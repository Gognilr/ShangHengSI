<script setup lang="ts">
import './illus.css'
// 文件审核 · 纸叠：三张纸错落，最上一张悬停时抬起；共 14 个元素
</script>

<template>
  <svg viewBox="0 0 220 120" fill="none" class="il il-stack" aria-hidden="true">
  <rect x="58" y="30" width="96" height="76" rx="7" class="il-p2" transform="rotate(-5 106 68)"/>
  <rect x="64" y="26" width="96" height="76" rx="7" class="il-p2" transform="rotate(3 112 64)"/>
  <g class="il-top">
    <rect x="60" y="18" width="100" height="80" rx="7" class="il-p"/>
    <path d="M74 36h40M74 48h64M74 72h56M74 84h30" class="il-l"/>
    <rect x="72" y="55" width="34" height="11" rx="3" class="il-c"/>
    <path d="M112 60.5h30" class="il-l"/>
    <path d="M40 60h20" class="il-a"/>
    <path d="M160 60h20" class="il-a il-tail"/>
  </g>
</svg>
</template>
