<script setup lang="ts">
import { useToast } from '../../composables/useToast'
import Icon from './Icon.vue'
const { toasts, dismiss } = useToast()
</script>
<template>
  <div class="toasts" aria-live="polite">
    <div v-for="toast in toasts" :key="toast.id" class="toast" :class="toast.kind" role="status">
      <span>{{ toast.message }}</span
      ><button class="icon-button" :aria-label="'通知を閉じる'" @click="dismiss(toast.id)">
        <Icon name="close" />
      </button>
    </div>
  </div>
</template>
