<script setup lang="ts">
import './illus.css'
// 合同对比 · 两张纸：中间的缎带把改动处缝起来，悬停时缎带走一遍；共 16 个元素
</script>

<template>
  <svg viewBox="0 0 220 120" fill="none" class="il il-pair" aria-hidden="true">
  <rect x="26" y="16" width="72" height="88" rx="7" class="il-p"/>
  <rect x="122" y="16" width="72" height="88" rx="7" class="il-p"/>
  <path d="M38 32h44M38 56h36M38 80h40M38 92h22" class="il-l"/>
  <path d="M134 32h44M134 80h40M134 92h30" class="il-l"/>
  <path d="M38 44h48" class="il-hw"/>
  <path d="M134 44h40" class="il-hw"/>
  <path d="M134 56h44M134 68h28" class="il-hn"/>
  <path class="il-rw il-rib" d="M98 40C110 40 110 40 122 40V48C110 48 110 48 98 48Z"/>
  <path class="il-rn il-rib" d="M98 54C110 54 110 52 122 52V72C110 72 110 58 98 58Z"/>
</svg>
</template>
