<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { request } from '../../api/client'

interface SocialSettings {
  bluesky: { configured: boolean; identifier: string; service_url: string }
  misskey: { configured: boolean; instance_url: string }
  accounts: SocialAccount[]
}
interface SocialAccount {
  id: string
  provider: 'bluesky' | 'misskey'
  configured: boolean
  label: string
  service_url?: string
  instance_url?: string
}

const settings = ref<SocialSettings | null>(null)
const loading = ref(true)
const busy = ref<'bluesky' | 'misskey' | null>(null)
const error = ref('')
const notice = ref('')
const bluesky = reactive({ service_url: 'https://bsky.social', identifier: '', app_password: '' })
const misskey = reactive({ instance_url: '' })
const misskeyFlowId = ref<string | null>(null)
let misskeyPopup: Window | null = null
let misskeyPollTimer: number | undefined
let misskeyFlowGeneration = 0

async function loadSettings() {
  loading.value = true
  error.value = ''
  try {
    settings.value = await request<SocialSettings>('/settings/social')
    bluesky.service_url = settings.value.bluesky.service_url || 'https://bsky.social'
    misskey.instance_url = settings.value.misskey.instance_url || ''
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'SNS 設定を読み込めませんでした'
  } finally {
    loading.value = false
  }
}
async function saveBluesky() {
  if (!bluesky.identifier.trim() || !bluesky.app_password) {
    error.value = 'Bluesky のアカウント名とアプリパスワードを入力してください'
    return
  }
  busy.value = 'bluesky'
  error.value = ''
  notice.value = ''
  try {
    settings.value = await request<SocialSettings>('/settings/social/bluesky', {
      method: 'PUT',
      body: JSON.stringify({ ...bluesky }),
    })
    bluesky.app_password = ''
    bluesky.identifier = ''
    notice.value = 'Bluesky アカウントを追加しました'
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Bluesky に接続できませんでした'
  } finally {
    busy.value = null
  }
}
function closeMisskeyPopup() {
  if (misskeyPopup && !misskeyPopup.closed) misskeyPopup.close()
  misskeyPopup = null
}
function clearMisskeyPoll() {
  if (misskeyPollTimer !== undefined) window.clearTimeout(misskeyPollTimer)
  misskeyPollTimer = undefined
}
async function cancelMisskeyAuth(showNotice = true) {
  const flowId = misskeyFlowId.value
  misskeyFlowGeneration += 1
  clearMisskeyPoll()
  misskeyFlowId.value = null
  closeMisskeyPopup()
  busy.value = null
  if (flowId) {
    try {
      await request(`/settings/social/misskey/miauth/${flowId}`, { method: 'DELETE' })
    } catch {
      // The server flow also expires automatically if cancellation cannot reach it.
    }
  }
  if (showNotice) {
    error.value = ''
    notice.value = 'Misskey 連携をキャンセルしました'
  }
}
function failMisskeyAuth(generation: number, message: string) {
  if (generation !== misskeyFlowGeneration) return
  const flowId = misskeyFlowId.value
  if (flowId)
    void request(`/settings/social/misskey/miauth/${flowId}`, { method: 'DELETE' }).catch(() => undefined)
  misskeyFlowId.value = null
  misskeyFlowGeneration += 1
  clearMisskeyPoll()
  closeMisskeyPopup()
  busy.value = null
  error.value = message
}
async function pollMisskeyAuth(flowId: string, generation: number, expiresAt: number) {
  if (generation !== misskeyFlowGeneration) return
  try {
    const result = await request<{
      state: 'pending' | 'connected' | 'expired' | 'cancelled'
      settings?: SocialSettings
    }>(`/settings/social/misskey/miauth/${flowId}/check`, { method: 'POST' })
    if (generation !== misskeyFlowGeneration) return
    if (result.state === 'connected' && result.settings) {
      settings.value = result.settings
      misskeyFlowId.value = null
      misskeyFlowGeneration += 1
      clearMisskeyPoll()
      closeMisskeyPopup()
      busy.value = null
      notice.value = 'Misskey アカウントを追加しました'
      return
    }
    if (result.state === 'expired') {
      failMisskeyAuth(generation, 'Misskey の認証期限が切れました。もう一度連携してください')
      return
    }
    if (result.state === 'cancelled') {
      failMisskeyAuth(generation, 'Misskey の認証処理がキャンセルされました')
      return
    }
    if (misskeyPopup?.closed) {
      failMisskeyAuth(generation, 'Misskey の認証画面が閉じられました。もう一度連携してください')
      return
    }
    if (Date.now() >= expiresAt) {
      failMisskeyAuth(generation, 'Misskey の認証期限が切れました。もう一度連携してください')
      return
    }
    misskeyPollTimer = window.setTimeout(() => {
      void pollMisskeyAuth(flowId, generation, expiresAt)
    }, 1500)
  } catch (cause) {
    failMisskeyAuth(
      generation,
      cause instanceof Error ? cause.message : 'Misskey の認証状態を確認できませんでした',
    )
  }
}
async function connectMisskey() {
  if (!misskey.instance_url.trim()) {
    error.value = 'Misskey のインスタンス URL を入力してください'
    return
  }
  const popup = window.open('about:blank', 'misskey-miauth', 'popup,width=520,height=720')
  if (!popup) {
    error.value = '認証画面を開けませんでした。ブラウザーでポップアップを許可してください'
    return
  }
  misskeyFlowGeneration += 1
  const generation = misskeyFlowGeneration
  misskeyPopup = popup
  popup.opener = null
  busy.value = 'misskey'
  error.value = ''
  notice.value = ''
  try {
    const flow = await request<{ flow_id: string; authorization_url: string; expires_in: number }>(
      '/settings/social/misskey/miauth',
      { method: 'POST', body: JSON.stringify({ instance_url: misskey.instance_url }) },
    )
    if (generation !== misskeyFlowGeneration) {
      await request(`/settings/social/misskey/miauth/${flow.flow_id}`, { method: 'DELETE' }).catch(
        () => undefined,
      )
      return
    }
    misskeyFlowId.value = flow.flow_id
    const authorizationUrl = new URL(flow.authorization_url)
    const instanceUrl = new URL(misskey.instance_url)
    if (authorizationUrl.origin !== instanceUrl.origin || !authorizationUrl.pathname.startsWith('/miauth/')) {
      throw new Error('Misskey の認証 URL が不正です')
    }
    popup.location.href = authorizationUrl.href
    notice.value = 'Misskey の画面で要求する権限を確認して許可してください'
    await pollMisskeyAuth(flow.flow_id, generation, Date.now() + flow.expires_in * 1000)
  } catch (cause) {
    failMisskeyAuth(generation, cause instanceof Error ? cause.message : 'Misskey に接続できませんでした')
  }
}
async function disconnect(account: SocialAccount) {
  busy.value = account.provider
  error.value = ''
  notice.value = ''
  try {
    settings.value = await request<SocialSettings>(`/settings/social/accounts/${account.id}`, {
      method: 'DELETE',
    })
    notice.value = `${account.label} の接続を解除しました`
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '接続を解除できませんでした'
  } finally {
    if (busy.value === account.provider) busy.value = null
  }
}

onMounted(loadSettings)
onBeforeUnmount(() => {
  void cancelMisskeyAuth(false)
})
</script>

<template>
  <section class="card social-settings">
    <h2>SNS 連携</h2>
    <p>
      再生画面のタイムラインからハッシュタグ検索や投稿ができます。認証情報はデータディレクトリに暗号化して保存し、画面やログには表示しません。
    </p>
    <p class="social-settings-privacy">
      このアプリにアクセスできる人は、接続済みアカウントで投稿できます。信頼できる LAN
      内だけで利用してください。
    </p>
    <p v-if="loading" role="status">接続設定を読み込み中…</p>
    <p v-if="error" class="inline-error" role="alert">{{ error }}</p>
    <p v-if="notice" class="status-success" role="status">{{ notice }}</p>

    <div class="social-setting-provider">
      <div class="social-setting-heading">
        <div>
          <h3>Bluesky</h3>
          <p class="social-connection-state">
            {{ settings?.accounts.filter((account) => account.provider === 'bluesky').length || 0 }}
            アカウント接続中
          </p>
        </div>
      </div>
      <ul
        v-if="settings?.accounts.some((account) => account.provider === 'bluesky')"
        class="social-connected-accounts"
      >
        <li
          v-for="account in settings.accounts.filter((item) => item.provider === 'bluesky')"
          :key="account.id"
        >
          <div>
            <strong>{{ account.label }}</strong>
            <small>{{ account.service_url }}</small>
          </div>
          <button
            class="button secondary small"
            type="button"
            :disabled="busy !== null"
            @click="disconnect(account)"
          >
            解除
          </button>
        </li>
      </ul>
      <div class="form-grid">
        <label class="field">
          PDS URL
          <input
            v-model.trim="bluesky.service_url"
            type="url"
            autocomplete="url"
            placeholder="https://bsky.social"
          />
        </label>
        <label class="field">
          ハンドルまたはメールアドレス
          <input
            v-model.trim="bluesky.identifier"
            autocomplete="username"
            placeholder="example.bsky.social"
          />
        </label>
        <label class="field social-secret-field">
          アプリパスワード
          <input
            v-model="bluesky.app_password"
            type="password"
            autocomplete="new-password"
            placeholder="接続時のみ入力"
          />
          <small>通常のアカウントパスワードではなく、Bluesky のアプリパスワードを使ってください。</small>
        </label>
      </div>
      <div class="settings-actions">
        <a
          class="social-doc-link"
          href="https://bsky.app/settings/app-passwords"
          target="_blank"
          rel="noopener noreferrer"
        >
          アプリパスワードを管理
        </a>
        <button class="button primary" type="button" :disabled="busy !== null" @click="saveBluesky">
          {{ busy === 'bluesky' ? '接続中…' : 'Bluesky アカウントを追加' }}
        </button>
      </div>
    </div>

    <div class="social-setting-provider">
      <div class="social-setting-heading">
        <div>
          <h3>Misskey</h3>
          <p class="social-connection-state">
            {{ settings?.accounts.filter((account) => account.provider === 'misskey').length || 0 }}
            アカウント接続中
          </p>
        </div>
      </div>
      <ul
        v-if="settings?.accounts.some((account) => account.provider === 'misskey')"
        class="social-connected-accounts"
      >
        <li
          v-for="account in settings.accounts.filter((item) => item.provider === 'misskey')"
          :key="account.id"
        >
          <div>
            <strong>{{ account.label }}</strong>
            <small>{{ account.instance_url }}</small>
          </div>
          <button
            class="button secondary small"
            type="button"
            :disabled="busy !== null"
            @click="disconnect(account)"
          >
            解除
          </button>
        </li>
      </ul>
      <label class="field">
        インスタンス URL
        <input
          v-model.trim="misskey.instance_url"
          type="url"
          autocomplete="url"
          placeholder="https://misskey.example"
          :disabled="busy !== null"
        />
      </label>
      <p class="social-connection-help">
        連携先の画面で権限を確認して許可します。検索と投稿、キャプチャ添付に必要な権限を要求します。
      </p>
      <div class="settings-actions">
        <button class="button primary" type="button" :disabled="busy !== null" @click="connectMisskey">
          {{ busy === 'misskey' ? 'Misskeyでの許可を待っています…' : 'Misskeyと連携' }}
        </button>
        <button v-if="busy === 'misskey'" class="button secondary" type="button" @click="cancelMisskeyAuth()">
          連携をキャンセル
        </button>
      </div>
    </div>
  </section>
</template>
