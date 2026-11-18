<script setup lang="ts">
import { ref } from 'vue'
import BidCellDetail from './BidCellDetail.vue'

// 单元格抽屉：内容见 BidCellDetail（与逐条核对共用）
defineProps<{ requirementId: string; bidderId: string }>()
const emit = defineEmits<{ close: [] }>()
const open = ref(true), title = ref('单元格')
</script>

<template>
  <el-drawer v-model="open" size="560px" append-to-body :title="title" @closed="emit('close')">
    <BidCellDetail :requirement-id="requirementId" :bidder-id="bidderId" no-bar @loaded="d => title = d.bidder.name" />
    <slot />
  </el-drawer>
</template>
