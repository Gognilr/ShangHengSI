<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { homeFor } from '@/utils/permissions'
// 统一的“页面不存在”（V33-B-03）：route 为未知地址；content 为整页内容打不开（不存在、已删除或他人的，接口一律 404，文案不区分）
const props = withDefaults(defineProps<{ kind?: 'route' | 'content' }>(), { kind: 'route' })

const COPY = {
  route: { title: '这一页已被脱敏', description: '其实它并不存在。地址可能输错了，或这个页面已经移除。' },
  content: { title: '这项内容无法打开', description: '要打开的内容不存在、已被删除，或您没有访问它的权限。如确属您本人的内容，请确认登录的是正确账号。' },
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
  <div class="page not-found">
    <div class="sheet" aria-hidden="true">
      <span class="stamp">查无此页</span>
      <span class="line" style="width: 72%" />
      <span class="line bar" style="width: 54%; --i: 0" />
      <div class="code">4<span class="zero bar" style="--i: 1">[页面_1]</span>4</div>
      <span class="line" style="width: 88%" />
      <span class="line split"><span class="seg" style="width: 34%" /><span class="chip">[机构_1]</span><span class="seg" style="flex: 1" /></span>
      <span class="line bar" style="width: 66%; --i: 2" />
      <span class="line" style="width: 46%" />
    </div>
    <h1 class="title">{{ copy.title }}</h1>
    <p class="description">{{ copy.description }}</p>
    <p class="path" :title="path">访问的路径：{{ path }}</p>
    <div class="actions">
      <el-button type="primary" @click="router.push(home)">{{ HOME_LABEL[home] }}</el-button>
      <el-button @click="router.push('/history')">查看历史记录</el-button>
    </div>
  </div>
</template>

<style scoped>
.not-found { display: flex; flex-direction: column; align-items: center; padding-top: 72px; text-align: center; }

/* 一页被脱敏的文档：文字线 + 黑条 + 一处代号，中间是 4▮4 */
.sheet { position: relative; display: flex; flex-direction: column; gap: 12px; width: 320px; padding: 28px 32px; background: var(--si-paper); box-shadow: var(--si-paper-shadow); border-radius: var(--si-radius-paper); }
.line { display: block; height: 8px; border-radius: 2px; background: var(--si-hair-strong); }
.split { display: flex; align-items: center; gap: 6px; height: auto; background: none; }
.seg { height: 8px; border-radius: 2px; background: var(--si-hair-strong); }
.chip { padding: 0 6px; border-radius: var(--si-radius-tag); background: var(--si-cap-bg); color: var(--si-cap-ink); font: var(--si-font-code-sm); }
.bar { background: var(--si-redact-bar); }
/* 404 是品牌图形（同 SiLogo），字号不在正文档位内 */
.code { display: flex; align-items: center; justify-content: center; gap: 8px; margin: 6px 0; color: var(--si-paper-ink); font-family: var(--si-font-sans); font-size: 80px; font-weight: 600; line-height: 1; }
.zero { display: inline-flex; align-items: center; justify-content: center; width: 72px; height: 62px; border-radius: 3px; color: color-mix(in srgb, var(--si-white) 78%, transparent); font: var(--si-font-code-sm); font-weight: 400; }
.stamp { position: absolute; top: -14px; right: -26px; padding: 3px 10px; border: 2px solid var(--si-danger); border-radius: 4px; color: var(--si-danger); font: var(--si-font-label); letter-spacing: 2px; opacity: .55; transform: rotate(-8deg); }

/* 进入时黑条依次落下盖住文字线，只播一次；特效关闭时 motion.css 统一去掉动画，直接是终态 */
.bar { animation: redact-drop 240ms var(--si-ease) backwards; animation-delay: calc(var(--i) * 80ms + 120ms); }
@keyframes redact-drop { from { opacity: 0; transform: translateY(-6px); } }

.title { margin: 32px 0 0; font: var(--si-font-title); color: var(--si-ink-1); }
.description { margin: 8px 0 0; max-width: 440px; font: var(--si-font-body); color: var(--si-ink-2); }
.path { margin: 12px 0 0; max-width: 440px; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; font: var(--si-font-code); color: var(--si-ink-3); }
.actions { margin-top: 24px; }
</style>
