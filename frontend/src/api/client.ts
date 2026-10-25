import type { ApiError } from './types'
import { ref } from 'vue'
const API_BASE = import.meta.env.VITE_API_URL || '/api'
export const databaseUnavailable = ref(false)
export class HttpError extends Error implements ApiError {
  status: number
  fields: Record<string, string>
  constructor(message: string, status: number, fields: Record<string, string> = {}) {
    super(message)
    this.name = 'HttpError'
    this.status = status
    this.fields = fields
  }
}
export function apiUrl(path: string): string {
  const base = API_BASE.replace(/\/$/, '')
  const normalized = path.startsWith('/') ? path : `/${path}`
  if (base.endsWith('/api') && normalized.startsWith('/api/')) return `${base.slice(0, -4)}${normalized}`
  return `${base}${normalized}`
}
export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(apiUrl(path), {
      ...init,
      headers: { ...(init.body ? { 'Content-Type': 'application/json' } : {}), ...init.headers },
    })
  } catch (error) {
    throw new HttpError(
      error instanceof Error ? `通信に失敗しました: ${error.message}` : '通信に失敗しました',
      0,
    )
  }
  const contentType = response.headers.get('content-type') || ''
  let payload: unknown
  if (contentType.includes('application/json')) {
    try {
      payload = await response.json()
    } catch {
      payload = null
    }
  } else {
    try {
      payload = await response.text()
    } catch {
      payload = null
    }
  }
  if (!response.ok) {
    const object = payload && typeof payload === 'object' ? (payload as Record<string, unknown>) : null
    const fieldsRaw = object?.fields
    const fields =
      fieldsRaw && typeof fieldsRaw === 'object'
        ? Object.fromEntries(
            Object.entries(fieldsRaw).filter(
              (entry): entry is [string, string] => typeof entry[1] === 'string',
            ),
          )
        : {}
    const message =
      typeof object?.error === 'string'
        ? object.error
        : typeof payload === 'string' && payload.trim()
          ? payload.slice(0, 300)
          : `要求に失敗しました (${response.status})`
    if (message === 'データベースに接続できません') databaseUnavailable.value = true
    throw new HttpError(message, response.status, fields)
  }
  databaseUnavailable.value = false
  return payload as T
}
export function jsonBody(value: unknown): RequestInit {
  return { method: 'POST', body: JSON.stringify(value) }
}
