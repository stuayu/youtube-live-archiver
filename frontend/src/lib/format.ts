export function formatDate(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  const p = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}/${p(date.getMonth() + 1)}/${p(date.getDate())} ${p(date.getHours())}:${p(date.getMinutes())}`
}
export function formatClock(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? '—'
    : `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}:${String(date.getSeconds()).padStart(2, '0')}`
}
export function relativeTime(value: string | null | undefined, now = Date.now()): string {
  if (!value) return '未確認'
  const delta = new Date(value).getTime() - now
  if (!Number.isFinite(delta)) return '—'
  const abs = Math.abs(delta)
  if (abs >= 365 * 86400000) return formatDate(value)
  const units: Array<[number, string]> = [
    [86400000, '日'],
    [3600000, '時間'],
    [60000, '分'],
    [1000, '秒'],
  ]
  const [size, unit] = units.find(([size]) => abs >= size) ?? units[3]!
  const amount = Math.max(1, Math.floor(abs / size))
  const remainder = abs - amount * size
  const nextUnit = units[units.findIndex(([unitSize]) => unitSize === size) + 1]
  const extra =
    nextUnit && remainder >= nextUnit[0] ? ` ${Math.floor(remainder / nextUnit[0])} ${nextUnit[1]}` : ''
  const parts = `${amount} ${unit}${extra}`
  return delta <= 0 ? `${parts}前` : `あと ${parts}`
}
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return '—'
  const total = Math.floor(seconds)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}
export function formatBytes(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value) || value < 0) return '—'
  if (value === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let size = value
  let unit = 0
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024
    unit++
  }
  return `${size.toFixed(unit ? 1 : 0)} ${units[unit]}`
}
export function gbToBytes(value: number | '' | null): number | null {
  if (value === '' || value === null) return null
  if (!Number.isFinite(value) || value < 0) return null
  return Math.round(value * 1024 ** 3)
}
export function bytesToGb(value: number | null): number | '' {
  return value === null ? '' : Number((value / 1024 ** 3).toFixed(2))
}
