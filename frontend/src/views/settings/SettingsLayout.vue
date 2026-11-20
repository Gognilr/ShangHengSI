<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, provide, ref } from 'vue'
import { settingSectionsKey, type SettingAnchor } from '@/utils/settingSections'
import { useFx } from '@/utils/motion'
import { useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { Cpu, Database, EyeOff, FileStack, LibraryBig, List, ListChecks, Scale, ShieldCheck, Tags, UserRound, Users } from '@lucide/vue'
import { SETTING_TABS } from '@/utils/permissions'

// 设置页导航与路由一一对应，分页按权限点显示，路由守卫再校验（V31-U5F-07）。
const auth = useAuthStore()
const ICONS = { personal: UserRound, users: Users, roles: ShieldCheck, model: Cpu, requirements: ListChecks, laws: Scale, templates: FileStack, knowledge: LibraryBig, 'bid-categories': Tags, redaction: EyeOff, data: Database } as Record<string, typeof List>
const tabs = computed(() => SETTING_TABS.filter(t => auth.can(...t.perms)).map(t => ({ ...t, icon: ICONS[t.name] ?? List })))
// 导航分两组：个人、管理（澄明 8.9）；没有可见项的组不显示
const GROUPS = [{ label: '个人', names: ['personal'] }, { label: '管理', names: ['users', 'roles', 'model', 'requirements', 'laws', 'templates', 'knowledge', 'bid-categories', 'redaction', 'data'] }]
const groups = computed(() => GROUPS.map(g => ({ label: g.label, items: g.names.flatMap(n => tabs.value.filter(t => t.name === n)) })).filter(g => g.items.length))

const anchors = ref<SettingAnchor[]>([]), activeSection = ref(''), root = ref<HTMLElement>(), fx = useFx()
let observer: IntersectionObserver | undefined
function observe() {
  observer?.disconnect()
  observer = new IntersectionObserver(entries => {
    const visible = entries.filter(e => e.isIntersecting).sort((a,b) => a.boundingClientRect.top-b.boundingClientRect.top)
    if (visible[0]) activeSection.value = visible[0].target.id
  }, { root: root.value, rootMargin: '-64px 0px -55% 0px', threshold: 0 })
  anchors.value.forEach(a => observer!.observe(a.element))
}
provide(settingSectionsKey, { add(section) { anchors.value.push(section); observe() }, remove(id) { anchors.value = anchors.value.filter(a => a.id !== id); observe() } })
function jump(section: SettingAnchor) { activeSection.value = section.id; section.element.scrollIntoView({ behavior: fx.value === 'off' ? 'instant' : 'smooth', block: 'start' }) }
onMounted(observe)
onBeforeUnmount(() => observer?.disconnect())
const route = useRoute()

const activeTab = computed(() => {
  const hit = tabs.value.find((t) => route.path === `/settings/${t.name}`)
  return hit ? hit.name : tabs.value[0]?.name ?? ''
})

</script>

<template>
  <!-- 设置区不是卡片：左列导航、一条细线、右列内容，只有右列滚动（V321Q-48，V34-A-06） -->
  <div class="page settings">
    <header class="head"><h1 class="entry-title">设置</h1><p class="entry-lede">个人外观与账号，以及管理员负责的账号、模型、审核要求和数据。</p></header>
    <div class="shell">
      <nav class="settings-nav" aria-label="设置导航">
        <template v-for="g in groups" :key="g.label">
          <p class="nav-group">{{ g.label }}</p>
          <router-link v-for="t in g.items" :key="t.name" :to="`/settings/${t.name}`" class="nav-item" :class="{ selected: activeTab === t.name }" :aria-current="activeTab === t.name ? 'page' : undefined">
            <component :is="t.icon" :size="16" :stroke-width="1.75" /><span>{{ t.label }}</span>
          </router-link>
        </template>
      </nav>
      <div ref="root" class="settings-content">
        <nav v-if="anchors.length >= 4" class="section-nav" aria-label="本页分节"><el-button v-for="section in anchors" :key="section.id" text :type="activeSection === section.id ? 'primary' : 'default'" :aria-current="activeSection === section.id ? 'location' : undefined" @click="jump(section)">{{ section.title }}</el-button></nav>
        <div class="settings-body">
          <router-view v-slot="{ Component }"><Transition name="si-page"><component :is="Component" :key="activeTab" /></Transition></router-view>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.settings { max-width: 1120px; height: 100%; box-sizing: border-box; display: flex; flex-direction: column; padding-top: 32px; padding-bottom: 24px; }
.settings .entry-lede { margin-bottom: 20px; }
.shell { flex: 1; min-height: 0; display: grid; grid-template-columns: 220px minmax(0, 1fr); gap: 24px; }
.settings-nav { align-self: start; display: flex; flex-direction: column; gap: 2px; max-height: 100%; padding: 12px; overflow-y: auto; box-sizing: border-box; background: var(--si-surface-1); border: 1px solid var(--si-hair); border-radius: var(--si-radius-card); box-shadow: var(--si-shadow-1); }
.nav-group { flex: none; margin: 14px 0 4px; padding: 0 10px; font: var(--si-font-overline); color: var(--si-ink-3); }
.nav-group:first-child { margin-top: 2px; }
.nav-item { flex: none; display: flex; align-items: center; gap: 10px; height: 38px; padding: 0 10px; border-radius: 10px; color: var(--si-ink-2); font: var(--si-font-label); text-decoration: none; transition: background-color var(--si-d2) var(--si-ease-out), color var(--si-d2) var(--si-ease-out); }
.nav-item svg { flex: none; color: var(--si-ink-3); transition: color var(--si-d2) var(--si-ease-out); }
.nav-item:hover { background: var(--si-hover-bg); color: var(--si-ink-1); }
.nav-item.selected { background: var(--si-blue-tint); color: var(--si-blue-ink); font-weight: 600; }
.nav-item.selected svg { color: var(--si-brand-2); }
.nav-item:focus-visible { outline-offset: -2px; }
.settings-content { min-width: 0; overflow-y: auto; padding: 2px 4px 8px 2px; }
.settings-body { padding: 0; }
/* 各页自带的外层面板不再画边框与底色：卡片由每个分组（SettingSection）自己画 */
.settings-body :deep(.panel) { padding: 0; border: 0; border-radius: 0; background: transparent; box-shadow: none; }
/* 表单标签宽 140，控件宽 360（澄明 8.9） */
.settings-body :deep(.si-row-label) { width: 140px; }
.section-nav { position: sticky; top: 0; z-index: 5; display: flex; overflow-x: auto; margin-bottom: 12px; padding: 6px 8px; background: var(--si-surface-1); border: 1px solid var(--si-hair); border-radius: 12px; box-shadow: var(--si-shadow-1); }
.section-nav :deep(.el-button) { flex: none; margin: 0; }
@media (max-width: 1279px) { .shell { grid-template-columns: 200px minmax(0, 1fr); gap: 16px; } }
</style>
