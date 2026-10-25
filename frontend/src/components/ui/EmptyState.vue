<script setup lang="ts">
defineProps<{ title: string; description?: string }>()
</script>

<template>
  <div class="empty-state">
    <b>{{ title }}</b>
    <p v-if="description">{{ description }}</p>
    <slot />
  </div>
</template>
