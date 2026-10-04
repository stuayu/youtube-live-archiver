<script setup lang="ts">
export type IconName =
  | 'home'
  | 'channels'
  | 'upcoming'
  | 'archive'
  | 'search'
  | 'storage'
  | 'analysis'
  | 'activity'
  | 'settings'
  | 'more'
  | 'refresh'
  | 'add'
  | 'close'
  | 'play'
  | 'download'

defineProps<{
  name: IconName
}>()
</script>

<template>
  <svg
    class="icon"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.8"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
  >
    <template v-if="name === 'home'">
      <path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z" />
    </template>
    <template v-else-if="name === 'channels'">
      <circle cx="9" cy="8" r="3.5" />
      <path d="M2.5 20a6.5 6.5 0 0 1 13 0M16 5.5a3.5 3.5 0 0 1 0 6.8M18 14a5.5 5.5 0 0 1 3.5 5" />
    </template>
    <template v-else-if="name === 'upcoming'">
      <rect x="3" y="4.5" width="18" height="17" rx="2" />
      <path d="M16 2.5v4M8 2.5v4M3 9h18m-9 3 2 2-2 2" />
    </template>
    <template v-else-if="name === 'archive'">
      <path d="M3 5h18v4H3zM5 9v12h14V9m-9 4h4" />
    </template>
    <template v-else-if="name === 'search'">
      <circle cx="10.8" cy="10.8" r="7.3" />
      <path d="m16.2 16.2 5 5" />
    </template>
    <template v-else-if="name === 'storage'">
      <ellipse cx="12" cy="5" rx="8.5" ry="3" />
      <path d="M3.5 5v7c0 1.7 3.8 3 8.5 3s8.5-1.3 8.5-3V5M3.5 12v7c0 1.7 3.8 3 8.5 3s8.5-1.3 8.5-3v-7" />
    </template>
    <template v-else-if="name === 'analysis'">
      <path d="M3 3v18h18M7 15l4-5 4 3 6-8" />
      <circle cx="7" cy="15" r=".6" fill="currentColor" />
      <circle cx="11" cy="10" r=".6" fill="currentColor" />
      <circle cx="15" cy="13" r=".6" fill="currentColor" />
      <circle cx="21" cy="5" r=".6" fill="currentColor" />
    </template>
    <template v-else-if="name === 'activity'">
      <path d="M2 12h4l3-8 5 16 3-8h5" />
    </template>
    <template v-else-if="name === 'settings'">
      <path d="M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Z" />
      <path
        d="m19.4 15 .1.1a1.8 1.8 0 1 1-2.5 2.5l-.1-.1a1.8 1.8 0 0 0-3 .9v.2a1.8 1.8 0 1 1-3.6 0v-.2
        a1.8 1.8 0 0 0-3-.9l-.1.1a1.8 1.8 0 1 1-2.5-2.5l-.1-.1a1.8 1.8 0 0 0-.9-3h-.2a1.8 1.8 0 1 1 0-3.6h.2
        a1.8 1.8 0 0 0 .9-3l-.1-.1a1.8 1.8 0 1 1 2.5-2.5l.1.1a1.8 1.8 0 0 0 3-.9v-.2a1.8 1.8 0 1 1 3.6 0v.2
        a1.8 1.8 0 0 0 3 .9l.1-.1a1.8 1.8 0 1 1 2.5 2.5l-.1.1a1.8 1.8 0 0 0 .9 3h.2a1.8 1.8 0 1 1 0 3.6h-.2
        a1.8 1.8 0 0 0-.9 3Z"
      />
    </template>
    <template v-else-if="name === 'refresh'">
      <path d="M20 7v5h-5M4 17v-5h5" />
      <path d="M5.6 9a7 7 0 0 1 11.7-2L20 12M4 12l2.7 5a7 7 0 0 0 11.7-2" />
    </template>
    <template v-else-if="name === 'add'"><path d="M12 5v14M5 12h14" /></template>
    <template v-else-if="name === 'close'"><path d="m6 6 12 12M18 6 6 18" /></template>
    <template v-else-if="name === 'more'">
      <circle cx="5" cy="12" r=".8" fill="currentColor" />
      <circle cx="12" cy="12" r=".8" fill="currentColor" />
      <circle cx="19" cy="12" r=".8" fill="currentColor" />
    </template>
    <template v-else-if="name === 'play'"><path d="m8 5 11 7-11 7z" fill="currentColor" /></template>
    <template v-else-if="name === 'download'"><path d="M12 3v12m-5-5 5 5 5-5M4 17v4h16v-4" /></template>
  </svg>
</template>
