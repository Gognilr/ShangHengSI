<script setup lang="ts">
import { computed } from 'vue'
import { BookOpen, ExternalLink, FileText, ShieldCheck } from '@lucide/vue'
import SiLogo from '@/components/brand/SiLogo.vue'
import { useAuthStore } from '@/stores/auth'

/** 账号菜单“关于”：产品、版本、作者、数据说明与链接。版本取 /auth/me 返回的 APP_VERSION。 */
const visible = defineModel<boolean>({ required: true })
const auth = useAuthStore()

const REPO = 'https://github.com/Gognilr/ShangHengSI'
const versionText = computed(() => {
  const v = auth.version
  if (!v || v === 'dev') return '开发版'
  return `V${v.replace(/^v/i, '')}`
})
</script>

<template>
  <el-dialog v-model="visible" width="420px" append-to-body class="about-dialog" :show-close="true" aria-label="关于商衡SI">
    <div class="about">
      <SiLogo :px="56" mark-only />
      <h2 class="name">商衡SI</h2>
      <p class="tagline">企业安全脱敏与智能审查平台</p>
      <p class="version">{{ versionText }}</p>
      <p class="author">李芮 设计与开发</p>

      <div class="safe">
        <ShieldCheck :size="16" :stroke-width="1.75" aria-hidden="true" />
        <span>文件在本地脱敏，云端模型只看到脱敏后的文本；原件不出服务器，导出文件不留存。</span>
      </div>

      <nav class="links" aria-label="相关链接">
        <a href="/manual.pdf" target="_blank" rel="noopener"><BookOpen :size="16" :stroke-width="1.75" />使用手册</a>
        <a :href="REPO" target="_blank" rel="noopener noreferrer"><ExternalLink :size="16" :stroke-width="1.75" />源码</a>
        <a href="/licenses/THIRD-PARTY.txt" target="_blank" rel="noopener"><FileText :size="16" :stroke-width="1.75" />第三方许可</a>
      </nav>

      <p class="legal">© 2026 李芮<br>源码公开 · FSL-1.1-ALv2（两年后转 Apache-2.0）</p>
    </div>
    <template #footer>
      <el-button type="primary" @click="visible = false">关闭</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.about { display: flex; flex-direction: column; align-items: center; text-align: center; padding: 4px 8px 0; }
.name { font: var(--si-font-title-lg); color: var(--si-ink-1); margin: 12px 0 4px; }
.tagline { font: var(--si-font-body); color: var(--si-ink-2); margin: 0; }
.version { font: var(--si-font-code-sm); color: var(--si-ink-3); margin: 8px 0 0; }
.author { font: var(--si-font-label); color: var(--si-ink-1); margin: 16px 0 0; }
.safe { display: flex; gap: 8px; align-items: flex-start; text-align: left; margin-top: 20px; padding: 12px 14px; border-radius: var(--si-radius-md); background: var(--si-ok-soft); color: var(--si-ok-ink); font: var(--si-font-meta); }
.safe svg { flex: none; margin-top: 2px; }
.links { display: flex; flex-wrap: wrap; justify-content: center; gap: 8px 20px; margin-top: 20px; }
.links a { display: inline-flex; align-items: center; gap: 6px; color: var(--si-blue-ink); font: var(--si-font-label); text-decoration: none; }
.links a:hover { text-decoration: underline; }
.legal { font: var(--si-font-caption); color: var(--si-ink-3); margin: 20px 0 0; padding-top: 14px; border-top: 1px solid var(--si-hair); align-self: stretch; }
</style>
