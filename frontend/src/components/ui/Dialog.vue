<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import Icon from './Icon.vue'
const props = defineProps<{ title: string; labelledby?: string }>()
const emit = defineEmits<{ close: [] }>()
const box = ref<HTMLElement | null>(null)
const close = (event: KeyboardEvent) => {
  if (event.key === 'Escape') emit('close')
  if (event.key === 'Tab' && box.value) {
    const nodes = [
      ...box.value.querySelectorAll<HTMLElement>(
        'button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href]',
      ),
    ]
    if (!nodes.length) return
    const first = nodes[0]!
    const last = nodes[nodes.length - 1]!
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }
}
onMounted(() => {
  document.body.classList.add('dialog-open')
  box.value?.focus()
  document.addEventListener('keydown', close)
})
onBeforeUnmount(() => {
  document.body.classList.remove('dialog-open')
  document.removeEventListener('keydown', close)
})
</script>
<template>
  <div class="dialog-backdrop" @mousedown.self="emit('close')">
    <section ref="box" class="dialog" role="dialog" aria-modal="true" :aria-label="props.title" tabindex="-1">
      <header class="dialog-header">
        <h2>{{ props.title }}</h2>
        <button class="icon-button" aria-label="閉じる" @click="emit('close')"><Icon name="close" /></button>
      </header>
      <div class="dialog-body"><slot /></div>
      <footer v-if="$slots.footer" class="dialog-footer"><slot name="footer" /></footer>
    </section>
  </div>
</template>
