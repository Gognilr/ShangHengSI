<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { FolderOpen } from '@lucide/vue'
import WorkFolderPicker from '@/components/WorkFolderPicker.vue'
import { clear, current, folderName, pick, supported } from '@/utils/workFolder'

// 工作文件夹入口一行（V3-A 7.2）：浏览器不支持时不显示；文件夹名只在本页显示
defineProps<{ disabled?: boolean }>()
const emit = defineEmits<{ add: [files: File[]] }>()
const pickerOpen = ref(false)

onMounted(() => { void current() })
async function choose() {
  try { await pick() }
  catch { ElMessage.error('没有该文件夹的访问权限，请重新选择') }
}
</script>

<template>
  <div v-if="supported" class="work-folder">
    <FolderOpen :size="16" :stroke-width="1.75" aria-hidden="true" />
    <template v-if="folderName">
      <span class="name">工作文件夹：{{ folderName }}</span>
      <el-button link type="primary" :disabled="disabled" @click="pickerOpen = true">从工作文件夹选择</el-button>
      <span class="sep">·</span>
      <el-button link type="primary" @click="choose">更换</el-button>
      <span class="sep">·</span>
      <el-button link type="primary" @click="clear">不使用</el-button>
    </template>
    <el-button v-else link type="primary" @click="choose">选择工作文件夹</el-button>
    <WorkFolderPicker v-model="pickerOpen" @add="emit('add', $event)" />
  </div>
</template>

<style scoped>
.work-folder { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-bottom: 12px; font: var(--si-font-meta); color: var(--el-text-color-secondary); }
.name { max-width: 240px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sep { color: var(--el-text-color-placeholder); }
</style>
