<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { homeFor } from '@/utils/permissions'
import { ClockFading, House } from '@lucide/vue'
import SiLogo from '@/components/brand/SiLogo.vue'
// 统一的“页面不存在”（V33-B-03）：route 为未知地址；content 为整页内容打不开（不存在、已删除或他人的，接口一律 404，文案不区分）
const props = withDefaults(defineProps<{ kind?: 'route' | 'content' }>(), { kind: 'route' })

const COPY = {
  route: { title: '这一页没找平', description: '地址可能写错了，或者这条记录已经过期删除。' },
  content: { title: '这项内容打不开', description: '要打开的内容不存在、已经删除，或者不属于你。如果确实是你自己的，请确认登录的是正确账号。' },
}
const HOME_LABEL: Record<string, string> = { '/assistant': '回到工作台', '/meeting': '回到会议纪要', '/translate': '回到翻译' }

const route = useRoute(), router = useRouter(), auth = useAuthStore()
const copy = computed(() => COPY[props.kind])
const home = computed(() => homeFor(auth.can))
// 只显示路径，不带查询参数与 # 之后的内容
const path = computed(() => { try { return decodeURI(route.path) } catch { return route.path } })

const baseTitle = document.title
onMounted(() => { document.title = '页面不存在 · 商衡SI' })
onBeforeUnmount(() => { document.title = baseTitle })
</script>

<template>
  <!-- 404 彩蛋（澄明 8.10）：128px 标志，横线倾斜 -->
  <div class="not-found">
    <SiLogo mark-only tilt :px="128" />
    <h1 class="title">{{ copy.title }}</h1>
    <p class="description">{{ copy.description }}</p>
    <p class="path" :title="path">{{ path }}</p>
    <div class="actions">
      <el-button type="primary" @click="router.push(home)"><House :size="16" :stroke-width="1.75" />{{ HOME_LABEL[home] }}</el-button>
      <el-button @click="router.push('/history')"><ClockFading :size="16" :stroke-width="1.75" />历史记录</el-button>
    </div>
  </div>
</template>

<style scoped>
.not-found { display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 100%; padding: 48px 24px; box-sizing: border-box; text-align: center; }
.title { margin: 24px 0 0; font: var(--si-font-title); color: var(--si-ink-1); }
.description { margin: 8px 0 0; max-width: 440px; font: var(--si-font-meta); color: var(--si-ink-3); }
.path { margin: 10px 0 0; max-width: 440px; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; font: var(--si-font-code-sm); color: var(--si-ink-4); }
.actions { display: flex; gap: 8px; margin-top: 24px; }
.actions svg { margin-right: 6px; }
</style>
