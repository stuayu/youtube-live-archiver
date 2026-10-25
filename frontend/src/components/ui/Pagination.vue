<script setup lang="ts">
defineProps<{ offset: number; limit: number; total: number }>()
const emit = defineEmits<{ change: [offset: number] }>()
</script>
<template>
  <nav class="pager" aria-label="ページ送り">
    <span>{{ total ? offset + 1 : 0 }}–{{ Math.min(offset + limit, total) }} / 全 {{ total }} 件</span>
    <div class="actions">
      <button
        class="button secondary"
        :disabled="offset <= 0"
        @click="emit('change', Math.max(0, offset - limit))"
      >
        前へ</button
      ><button
        class="button secondary"
        :disabled="offset + limit >= total"
        @click="emit('change', offset + limit)"
      >
        次へ
      </button>
    </div>
  </nav>
</template>
