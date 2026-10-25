<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { request } from '../../api/client'

interface SocialSettings {
  bluesky: { configured: boolean; identifier: string; service_url: string }
  misskey: { configured: boolean; instance_url: string }
}

const settings = ref<SocialSettings | null>(null)
const loading = ref(true)
const busy = ref<'bluesky' | 'misskey' | null>(null)
const error = ref('')
const notice = ref('')
const bluesky = reactive({ service_url: 'https://bsky.social', identifier: '', app_password: '' })
const misskey = reactive({ instance_url: '', token: '' })

async function loadSettings() {
  loading.value = true
  error.value = ''
  try {
    settings.value = await request<SocialSettings>('/settings/social')
    bluesky.service_url = settings.value.bluesky.service_url || 'https://bsky.social'
    bluesky.identifier = settings.value.bluesky.identifier || ''
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
    notice.value = 'Bluesky に接続しました'
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Bluesky に接続できませんでした'
  } finally {
    busy.value = null
  }
}
async function saveMisskey() {
  if (!misskey.instance_url.trim() || !misskey.token) {
    error.value = 'Misskey のインスタンス URL とアクセストークンを入力してください'
    return
  }
  busy.value = 'misskey'
  error.value = ''
  notice.value = ''
  try {
    settings.value = await request<SocialSettings>('/settings/social/misskey', {
      method: 'PUT',
      body: JSON.stringify({ ...misskey }),
    })
    misskey.token = ''
    notice.value = 'Misskey に接続しました'
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Misskey に接続できませんでした'
  } finally {
    busy.value = null
  }
}
async function disconnect(provider: 'bluesky' | 'misskey') {
  busy.value = provider
  error.value = ''
  notice.value = ''
  try {
    settings.value = await request<SocialSettings>(`/settings/social/${provider}`, { method: 'DELETE' })
    notice.value = `${provider === 'bluesky' ? 'Bluesky' : 'Misskey'} の接続を解除しました`
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '接続を解除できませんでした'
  } finally {
    busy.value = null
  }
}

onMounted(loadSettings)
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
          <p v-if="settings?.bluesky.configured" class="social-connection-state">
            接続済み · {{ settings.bluesky.identifier }}
          </p>
          <p v-else class="social-connection-state">未接続</p>
        </div>
        <button
          v-if="settings?.bluesky.configured"
          class="button secondary small"
          type="button"
          :disabled="busy !== null"
          @click="disconnect('bluesky')"
        >
          接続解除
        </button>
      </div>
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
          {{ busy === 'bluesky' ? '接続中…' : 'Bluesky に接続' }}
        </button>
      </div>
    </div>

    <div class="social-setting-provider">
      <div class="social-setting-heading">
        <div>
          <h3>Misskey</h3>
          <p v-if="settings?.misskey.configured" class="social-connection-state">
            接続済み · {{ settings.misskey.instance_url }}
          </p>
          <p v-else class="social-connection-state">未接続</p>
        </div>
        <button
          v-if="settings?.misskey.configured"
          class="button secondary small"
          type="button"
          :disabled="busy !== null"
          @click="disconnect('misskey')"
        >
          接続解除
        </button>
      </div>
      <div class="form-grid">
        <label class="field">
          インスタンス URL
          <input
            v-model.trim="misskey.instance_url"
            type="url"
            autocomplete="url"
            placeholder="https://misskey.example"
          />
        </label>
        <label class="field social-secret-field">
          アクセストークン
          <input
            v-model="misskey.token"
            type="password"
            autocomplete="new-password"
            placeholder="接続時のみ入力"
          />
          <small
            >アカウント設定で作成し、read:account・read:notes・write:notes・write:drive
            を許可してください。</small
          >
        </label>
      </div>
      <div class="settings-actions">
        <button class="button primary" type="button" :disabled="busy !== null" @click="saveMisskey">
          {{ busy === 'misskey' ? '接続中…' : 'Misskey に接続' }}
        </button>
      </div>
    </div>
  </section>
</template>
