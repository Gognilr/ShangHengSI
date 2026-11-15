<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, provide, ref } from 'vue'
import { settingSectionsKey, type SettingAnchor } from '@/utils/settingSections'
import { useFx } from '@/utils/motion'
import { useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { Cpu, Database, EyeOff, FileStack, LibraryBig, List, ListChecks, Scale, ShieldCheck, UserRound, Users } from '@lucide/vue'
import { SETTING_TABS } from '@/utils/permissions'

// 设置页导航与路由一一对应，分页按权限点显示，路由守卫再校验（V31-U5F-07）。
const auth = useAuthStore()
const ICONS = { personal: UserRound, users: Users, roles: ShieldCheck, model: Cpu, requirements: ListChecks, laws: Scale, templates: FileStack, knowledge: LibraryBig, redaction: EyeOff, data: Database } as Record<string, typeof List>
const tabs = computed(() => SETTING_TABS.filter(t => auth.can(...t.perms)).map(t => ({ ...t, icon: ICONS[t.name] ?? List })))
// 导航分三组（V321Q-51）；没有可见项的组不显示
const GROUPS = [{ label: '我的', names: ['personal'] }, { label: '内容', names: ['requirements', 'laws', 'templates', 'knowledge', 'redaction'] }, { label: '系统', names: ['users', 'roles', 'model', 'data'] }]
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
  <div class="page page--wide settings">
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
.settings { height: 100%; box-sizing: border-box; padding-bottom: 24px; }
.shell { height: 100%; box-sizing: border-box; padding: 0; display: grid; grid-template-columns: 200px minmax(0, 1fr); overflow: hidden; }
.settings-nav { display: flex; flex-direction: column; gap: 2px; padding: 0 12px 0 0; overflow-y: auto; border-right: 1px solid var(--si-hair); background: transparent; }
.nav-group { flex: none; margin: 12px 0 2px; padding: 0 12px; font: var(--si-font-overline); color: var(--si-ink-3); }
.nav-group:first-child { margin-top: 0; }
.nav-item { flex: none; display: flex; align-items: center; gap: 10px; height: 36px; padding: 0 12px; border-radius: var(--el-border-radius-base); color: var(--si-ink-2); font: var(--si-font-label); text-decoration: none; transition: background-color var(--si-dur-1) var(--si-ease), color var(--si-dur-1) var(--si-ease); }
.nav-item svg { flex: none; color: var(--si-ink-3); transition: color var(--si-dur-1) var(--si-ease); }
.nav-item:hover { background: var(--si-hover-bg); color: var(--si-ink-1); }
.nav-item.selected { background: var(--si-select-bg); color: var(--si-ink-1); font-weight: 600; }
.nav-item:focus-visible { outline-offset: -2px; }
.settings-content { min-width: 0; overflow-y: auto; }
.settings-body { padding: 0 0 8px 32px; }
/* 各页原来自带的面板在外框内不再画边框与底色 */
.settings-body :deep(.panel) { padding: 0; border: 0; border-radius: 0; background: transparent; box-shadow: none; }
.section-nav { position: sticky; top: 0; z-index: 5; display: flex; overflow-x: auto; padding: 8px 0 8px 32px; background: var(--si-bg-canvas); border-bottom: 1px solid var(--si-hair-faint); }
.section-nav :deep(.el-button) { flex: none; margin: 0; }
</style>
