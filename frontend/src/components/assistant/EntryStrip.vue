<script setup lang="ts">
// 工作台首页“办一件完整的事”入口格带（V34-A-04，visual.md 7.2）：四格共线、无底色无阴影；
// 每格一幅墨线小图（只有一笔松果赭）、等宽序号、标题、两行说明与一行实时状态。没有权限的格由页面去掉，其余等分。
import { ArrowRight } from '@lucide/vue'

export interface Entry { key: 'review' | 'bid' | 'meeting' | 'translate'; to: string; title: string; desc: string; status: string; disabled?: boolean }
defineProps<{ entries: Entry[] }>()
</script>

<template>
  <nav class="entry-strip" aria-label="办一件完整的事" :style="{ '--cols': entries.length }">
    <component :is="entry.disabled ? 'div' : 'router-link'" v-for="(entry, index) in entries" :key="entry.key" :to="entry.disabled ? undefined : entry.to"
               class="cell" :class="{ 'is-disabled': entry.disabled }" :aria-disabled="entry.disabled || undefined">
      <div class="cell-top">
        <svg class="art" viewBox="0 0 72 44" width="72" height="44" aria-hidden="true" fill="none" stroke-linecap="round" stroke-linejoin="round">
          <!-- 文件审核：一页文字，其中一行被标出并引出批注框 -->
          <template v-if="entry.key === 'review'">
            <rect x="6" y="3" width="30" height="38" rx="2" />
            <path d="M11 11h20M11 17h20M11 29h20M11 35h12" />
            <path class="warm" d="M11 23h20" />
            <path d="M32 23h10" />
            <rect x="42" y="16" width="25" height="14" rx="2" />
            <path d="M46 21h17M46 25h10" />
          </template>
          <!-- 标书初审：三份并排的文件，其中两份的同一行被标出并用括线连起 -->
          <template v-else-if="entry.key === 'bid'">
            <rect x="3" y="5" width="19" height="30" rx="2" />
            <rect x="27" y="5" width="19" height="30" rx="2" />
            <rect x="51" y="5" width="19" height="30" rx="2" />
            <path d="M7 11h11M7 16h11M31 11h11M31 16h11M55 11h11M55 16h11M55 21h11M7 27h8M31 27h8M55 27h8" />
            <path class="warm" d="M7 21h11M31 21h11M12.5 36v4h24v-4" />
          </template>
          <!-- 会议纪要：声波经箭头变成条目 -->
          <template v-else-if="entry.key === 'meeting'">
            <path d="M4 22v0M8 17v10M12 12v20M16 18v8M20 9v26M24 15v14M28 20v4" />
            <path class="warm" d="M34 22h9M40 18.5l3.5 3.5-3.5 3.5" />
            <path d="M50 12h1M55 12h13M50 22h1M55 22h13M50 32h1M55 32h9" />
          </template>
          <!-- 翻译：左右两栏文字，中间一对往返箭头 -->
          <template v-else>
            <rect x="3" y="5" width="24" height="34" rx="2" />
            <rect x="45" y="5" width="24" height="34" rx="2" />
            <path d="M8 12h14M8 18h14M8 24h14M8 30h9M50 12h14M50 18h14M50 24h14M50 30h9" />
            <path class="warm" d="M31 18h10M38 15l3 3-3 3M41 26H31M34 23l-3 3 3 3" />
          </template>
        </svg>
        <span class="no">{{ String(index + 1).padStart(2, '0') }}</span>
      </div>
      <span class="title">{{ entry.title }}<ArrowRight v-if="!entry.disabled" class="arrow" :size="15" :stroke-width="1.75" aria-hidden="true" /></span>
      <span class="desc">{{ entry.desc }}</span>
      <span class="status">{{ entry.status }}</span>
    </component>
  </nav>
</template>

<style scoped>
.entry-strip { display: grid; grid-template-columns: repeat(var(--cols), minmax(0, 1fr)); border-top: 1px solid var(--si-hair-strong); border-bottom: 1px solid var(--si-hair); }
.cell { position: relative; display: flex; flex-direction: column; gap: 4px; min-width: 0; padding: 16px 16px 14px; color: var(--si-ink-1); text-decoration: none; transition: background-color var(--si-dur-1) var(--si-ease); }
.cell + .cell { border-left: 1px solid var(--si-hair-faint); }
/* 悬停：整格浅底，上沿一条 2px 松青线自左向右展开，标题右侧滑出箭头；特效关闭时动画由 motion.css 统一取消，线直接出现 */
.cell::before { content: ""; position: absolute; top: -1px; left: 0; right: 0; height: 2px; background: var(--si-brand); transform: scaleX(0); transform-origin: left; transition: transform 280ms var(--si-ease); }
.cell:not(.is-disabled):hover, .cell:focus-visible { background: var(--si-hover-bg); }
.cell:not(.is-disabled):hover::before, .cell:focus-visible::before { transform: scaleX(1); }
.cell.is-disabled { cursor: default; }
.cell.is-disabled .art, .cell.is-disabled .title { opacity: .55; }
.cell-top { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 8px; }
.art { stroke: var(--si-ink-2); stroke-width: 1.25; }
.art .warm { stroke: var(--si-accent); }
.no { font: var(--si-font-code-sm); color: var(--si-ink-4); }
.title { display: flex; align-items: center; gap: 4px; font: 600 16px/1.4 var(--si-font-sans); }
.arrow { opacity: 0; transform: translateX(-6px); transition: opacity var(--si-dur-1) var(--si-ease), transform var(--si-dur-1) var(--si-ease); color: var(--si-ink-3); }
.cell:hover .arrow, .cell:focus-visible .arrow { opacity: 1; transform: none; }
.desc { min-height: calc(2 * 1.5em); font: var(--si-font-meta); color: var(--si-ink-3); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.status { margin-top: 6px; font: var(--si-font-caption); color: var(--si-ink-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
@media (max-width: 720px) { .entry-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); } .cell:nth-child(3) { border-left: 0; } .cell:nth-child(n+3) { border-top: 1px solid var(--si-hair-faint); } }
</style>
