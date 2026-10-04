import { computed, ref, watch } from 'vue'
export const tabs = [
  'home',
  'channels',
  'upcoming',
  'videos',
  'search',
  'storages',
  'analysis',
  'activity',
  'settings',
] as const
export type Tab = (typeof tabs)[number]
const queryKeys = [
  'channel',
  'q',
  'vstatus',
  'reason',
  'from',
  'to',
  'min_size',
  'max_size',
  'storage_id',
  'permanent',
  'sort',
  'direction',
] as const
export type QueryKey = (typeof queryKeys)[number]
export function readQuery(search = location.search): { tab: Tab; values: Partial<Record<QueryKey, string>> } {
  const p = new URLSearchParams(search)
  const raw = p.get('tab')
  const tab: Tab = tabs.includes(raw as Tab) ? (raw as Tab) : 'home'
  const values: Partial<Record<QueryKey, string>> = {}
  for (const key of queryKeys) {
    const value = p.get(key)
    if (value !== null) values[key] = value
  }
  return { tab, values }
}
export function useQueryState() {
  const initial = readQuery()
  const tab = ref<Tab>(initial.tab)
  const values = ref<Partial<Record<QueryKey, string>>>(initial.values)
  const params = computed(() => {
    const next = new URLSearchParams()
    if (tab.value !== 'home') next.set('tab', tab.value)
    for (const key of queryKeys) {
      const value = values.value[key]
      if (value && !(key === 'direction' && value === 'desc') && !(key === 'sort' && value === 'date'))
        next.set(key, value)
    }
    return next
  })
  watch(
    params,
    (next) => history.replaceState(null, '', `${location.pathname}${next.size ? `?${next}` : ''}`),
    { immediate: true },
  )
  const restore = () => {
    const restored = readQuery()
    tab.value = restored.tab
    values.value = restored.values
  }
  window.addEventListener('popstate', restore)
  return { tab, values, params, dispose: () => window.removeEventListener('popstate', restore) }
}
