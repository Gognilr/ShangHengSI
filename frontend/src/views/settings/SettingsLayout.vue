<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, provide, ref } from 'vue'
import { settingSectionsKey, type SettingAnchor } from '@/utils/settingSections'
import { useFx } from '@/utils/motion'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { User, Cpu, List, Lock, Database, UserRound, ShieldCheck } from '@lucide/vue'
import { SETTING_TABS } from '@/utils/permissions'

// 设置页导航与路由一一对应，分页按权限点显示，路由守卫再校验（V31-U5F-07）。
const auth = useAuthStore()
const ICONS = { personal: UserRound, users: User, roles: ShieldCheck, model: Cpu, redaction: Lock, data: Database } as Record<string, typeof List>
const tabs = computed(() => SETTING_TABS.filter(t => auth.can(...t.perms)).map(t => ({ ...t, icon: ICONS[t.name] ?? List })))

const anchors = ref<SettingAnchor[]>([]), activeSection = ref(''), root = ref<HTMLElement>(), fx = useFx()
let observer: IntersectionObserver | undefined
function observe() {
  observer?.disconnect()
  observer = new IntersectionObserver(entries => {
    const visible = entries.filter(e => e.isIntersecting).sort((a,b) => a.boundingClientRect.top-b.boundingClientRect.top)
    if (visible[0]) activeSection.value = visible[0].target.id
  }, { root: root.value?.closest('.main'), rootMargin: '-64px 0px -55% 0px', threshold: 0 })
  anchors.value.forEach(a => observer!.observe(a.element))
}
provide(settingSectionsKey, { add(section) { anchors.value.push(section); observe() }, remove(id) { anchors.value = anchors.value.filter(a => a.id !== id); observe() } })
function jump(section: SettingAnchor) { activeSection.value = section.id; section.element.scrollIntoView({ behavior: fx.value === 'off' ? 'instant' : 'smooth', block: 'start' }) }
onMounted(observe)
onBeforeUnmount(() => observer?.disconnect())
const route = useRoute()
const router = useRouter()

const activeTab = computed(() => {
  const hit = tabs.value.find((t) => route.path === `/settings/${t.name}`)
  return hit ? hit.name : tabs.value[0]?.name ?? ''
})

function onTabChange(name: string | number) {
  router.push(`/settings/${name}`)
}
</script>

<template>
  <div class="page page--wide settings">
    <aside class="panel settings-nav" aria-label="设置导航">
      <el-menu :default-active="activeTab" @select="onTabChange"><el-menu-item v-for="t in tabs" :key="t.name" :index="t.name"><el-icon><component :is="t.icon" :size="16" :stroke-width="1.75" /></el-icon><span>{{ t.label }}</span></el-menu-item></el-menu>
    </aside>
    <div ref="root" class="settings-content"><nav v-if="anchors.length >= 4" class="section-nav" aria-label="本页分节"><el-button v-for="section in anchors" :key="section.id" text :type="activeSection === section.id ? 'primary' : 'default'" :aria-current="activeSection === section.id ? 'location' : undefined" @click="jump(section)">{{ section.title }}</el-button></nav><router-view /></div>
  </div>
</template>

<style scoped>
.settings {
  display: flex;
  align-items: flex-start;
  gap: 20px;
}
.settings-nav { flex: 0 0 220px; box-sizing: border-box; padding: 12px; position: sticky; top: 32px; }
/* 主滚动区从顶栏下开始，32px 对应视口顶部的 64 + 32 = 96px。 */
.settings-nav :deep(.el-menu) { border-right: 0; }
.settings-nav :deep(.el-menu-item) { height: 44px; border-radius: 8px; }
.settings-nav :deep(.el-menu-item.is-active) { background: var(--el-color-primary-light-9); font-weight: 600; }
.settings-nav :deep(.el-menu-item .el-icon) { margin-right: 6px; }
.settings-content { flex: 1; min-width: 0; max-width: 960px; }
.section-nav { position: sticky; top: 0; z-index: 5; background: var(--si-surface-1); border-bottom: 1px solid var(--si-hair); display: flex; overflow-x: auto; padding: 8px 0; }
.section-nav :deep(.el-button) { flex: none; margin: 0; }
</style>
