import { ref } from 'vue'
export interface Toast {
  id: number
  kind: 'success' | 'error'
  message: string
}
const toasts = ref<Toast[]>([])
let sequence = 0
export function useToast() {
  function push(message: string, kind: Toast['kind'] = 'success') {
    const id = ++sequence
    toasts.value.push({ id, kind, message })
    window.setTimeout(() => dismiss(id), 5000)
  }
  function dismiss(id: number) {
    toasts.value = toasts.value.filter((item) => item.id !== id)
  }
  return { toasts, push, dismiss }
}
