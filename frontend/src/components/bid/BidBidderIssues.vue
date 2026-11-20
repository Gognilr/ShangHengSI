<script setup lang="ts">
import { computed, inject, ref } from 'vue'
import { bidApi, type Bidder } from '@/api/bid'
import SiSegmented from '@/components/ui/SiSegmented.vue'
import BidItemCard from './BidItemCard.vue'
import BidIssues from './BidIssues.vue'
import BidLede from './BidLede.vue'
import BidSignSheets from './BidSignSheets.vue'
import { bidResultsKey } from './results'

// 各投标人：这一家的严重问题与提示｜涉及的串标线索｜签章页（V35-E-01 去掉核对进度与人工核对行）。
const props = defineProps<{ bidders: Bidder[] }>()
const current = defineModel<string>({ required: true })
const ctx = inject(bidResultsKey)!
const options = computed(() => props.bidders.map(b => ({ value: b.id, label: b.name.length > 14 ? b.name.slice(0, 14) + '…' : b.name })))
const rename = ref(false), nameValue = ref(''), renameError = ref(''), renaming = ref(false)
const names = ref<Record<string, string>>({})
async function saveName() {
  if (!nameValue.value.trim() || renaming.value) return
  renaming.value = true; renameError.value = ''
  try {
    const project = await bidApi.renameBidder(ctx.projectId, current.value, nameValue.value.trim())
    names.value = Object.fromEntries(project.bidders.map(b => [b.id, b.name]))
    ctx.data.value = await bidApi.check(ctx.projectId)
    emit('renamed')
    rename.value = false
  } catch { renameError.value = '保存失败，请重试' }
  finally { renaming.value = false }
}
const emit = defineEmits<{ renamed: [] }>()
const name = computed(() => names.value[current.value] ?? ctx.names.value[current.value] ?? '')
const mine = computed(() => (ctx.data.value?.items ?? []).filter(i => i.bidder_ids.includes(current.value)))
const clues = computed(() => mine.value.filter(i => i.kind === 'clue'))
const pages = computed(() => (ctx.data.value?.signing ?? []).filter(s => s.bidder_id === current.value))
const marked = computed(() => pages.value.filter(s => s.mark).length)
const counts = computed(() => ctx.data.value?.conclusion.bidders.find(b => b.id === current.value))
</script>

<template>
  <div class="bid-tab">
    <BidLede :text="`${name}：严重问题 ${counts?.severe ?? 0} 条，提示 ${counts?.hint ?? 0} 条`" />
    <div>
      <SiSegmented v-if="bidders.length > 1" v-model="current" class="seg" :options="options" ariaLabel="投标人" />
      <template v-if="rename">
        <el-input v-model="nameValue" maxlength="255" aria-label="投标人名称" @keyup.enter="saveName" @keyup.esc="rename = false" />
        <el-button size="small" :disabled="renaming" @click="saveName">保存</el-button>
        <el-button size="small" text @click="rename = false">取消</el-button>
        <p v-if="renameError" role="alert">{{ renameError }}</p>
      </template>
      <el-button v-else size="small" text @click="nameValue = name; rename = true">修改名称</el-button>
    </div>

    <BidIssues :bidder="current" />

    <section v-if="clues.length" class="bid-block">
      <h3 class="bid-title">涉及的串标线索<span class="n">{{ clues.length }}</span></h3>
      <div class="list"><BidItemCard v-for="item in clues" :key="item.id" :item="item" /></div>
    </section>

    <section class="bid-block">
      <h3 class="bid-title">签章页<span class="n">{{ marked }} / {{ pages.length }}</span><span class="end">点缩略图看大图</span></h3>
      <BidSignSheets v-if="pages.length" :pages="pages" />
      <p v-if="!pages.length" class="bid-empty">没有在这一家的文件中找到投标函、授权委托书、报价表、承诺函等要求盖章签字的格式页；扫描页识别完成后会补上。</p>
    </section>

  </div>
</template>

<style scoped>
.seg { align-self: flex-start; }
.list { border-top: 1px solid var(--si-hair-strong); }
</style>
