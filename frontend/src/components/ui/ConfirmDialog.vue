<script setup lang="ts">
import Dialog from './Dialog.vue'

defineProps<{ title: string; body: string }>()
const emit = defineEmits<{ close: []; confirm: [] }>()
</script>

<template>
  <Dialog :title="title" @close="emit('close')">
    <p>{{ body }}</p>
    <template #footer>
      <button class="button secondary" @click="emit('close')">キャンセル</button>
      <button class="button danger" @click="emit('confirm')">実行</button>
    </template>
  </Dialog>
</template>
