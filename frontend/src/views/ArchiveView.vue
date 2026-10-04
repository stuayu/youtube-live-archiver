<script setup lang="ts">
import { reactive } from 'vue'
import { useWorkspaceContext } from '../composables/workspaceContext'
import Icon from '../components/ui/Icon.vue'
import type { QueryKey } from '../composables/useQueryState'
const ctx = reactive(useWorkspaceContext())
function filterName(key: QueryKey) {
  return {
    q: 'キーワード',
    channel: 'チャンネル',
    vstatus: '状態',
    reason: '保護理由',
    from: '開始日',
    to: '終了日',
    min_size: '最小サイズ',
    max_size: '最大サイズ',
    storage_id: '保存先',
    permanent: '保存期限',
    sort: '並び順',
    direction: '順序',
  }[key]
}
function filterValue(key: QueryKey) {
  const value = ctx.values[key]
  if (key === 'channel') return ctx.channels.find((item) => item.id === value)?.name || value
  if (key === 'vstatus' && value) return ctx.videoStatus(value).label
  if (key === 'permanent') return value === 'true' ? '永久' : value === 'false' ? '期限あり' : value
  if (key === 'reason' && value) return ctx.protectionReason(value)
  return value
}
function hasFilter(key: QueryKey) {
  return ctx.values[key] !== undefined && ctx.values[key] !== '' && key !== 'sort' && key !== 'direction'
}
</script>
<template>
  <section>
    <header class="page-head">
      <div>
        <h1>アーカイブ</h1>
        <p>{{ ctx.total }} 件の動画 · {{ ctx.scopeDeleted ? '削除済みの履歴' : '保存中の録画' }}</p>
      </div>
      <button class="button" @click="ctx.manualVideoOpen = true">URL から追加</button>
      <div class="segmented">
        <button :class="{ active: !ctx.scopeDeleted }" @click="ctx.setArchiveScope(false)">録画</button>
        <button :class="{ active: ctx.scopeDeleted }" @click="ctx.setArchiveScope(true)">
          削除済みの履歴
        </button>
      </div>
    </header>
    <div class="toolbar archive-toolbar">
      <label class="field grow"
        >キーワード<input v-model="ctx.values.q" placeholder="タイトル・説明" />
      </label>
      <label class="field archive-channel-filter"
        >チャンネル<select v-model="ctx.values.channel">
          <option :value="undefined">すべて</option>
          <option v-for="channel in ctx.channels" :key="channel.id" :value="channel.id">
            {{ channel.name }}
          </option>
        </select>
      </label>
      <label class="field archive-status-filter"
        >状態<select v-model="ctx.values.vstatus">
          <option :value="undefined">すべて</option>
          <option
            v-for="state in [
              'RECORDING',
              'ACTIVE',
              'RECORDING_FAILED',
              'WAITING_FOR_STORAGE',
              'VERIFYING_REMOTE',
              'DELETE_ELIGIBLE',
              'PROTECTION_PENDING',
              'PROTECTED',
              'SKIPPED',
              'CANCELLED',
            ]"
            :key="state"
            :value="state"
          >
            {{ ctx.videoStatus(state).label }}
          </option>
        </select>
      </label>
      <label class="field archive-sort-filter"
        >並び順<select v-model="ctx.values.sort">
          <option value="date">録画日時</option>
          <option value="size">容量</option>
          <option value="duration">長さ</option>
          <option value="title">タイトル</option>
        </select>
      </label>
      <button class="button secondary" @click="ctx.filtersOpen = !ctx.filtersOpen">
        {{ ctx.filtersOpen ? '絞り込みを閉じる' : '絞り込み' }}
      </button>
    </div>
    <div v-if="ctx.filtersOpen" class="card filters">
      <div class="mobile-archive-filters">
        <label class="field"
          >チャンネル<select v-model="ctx.values.channel">
            <option :value="undefined">すべて</option>
            <option v-for="channel in ctx.channels" :key="channel.id" :value="channel.id">
              {{ channel.name }}
            </option>
          </select></label
        >
        <label class="field"
          >状態<select v-model="ctx.values.vstatus">
            <option :value="undefined">すべて</option>
            <option
              v-for="state in [
                'RECORDING',
                'ACTIVE',
                'RECORDING_FAILED',
                'WAITING_FOR_STORAGE',
                'VERIFYING_REMOTE',
                'DELETE_ELIGIBLE',
                'PROTECTION_PENDING',
                'PROTECTED',
                'SKIPPED',
                'CANCELLED',
              ]"
              :key="state"
              :value="state"
            >
              {{ ctx.videoStatus(state).label }}
            </option>
          </select></label
        >
        <label class="field"
          >並び順<select v-model="ctx.values.sort">
            <option value="date">録画日時</option>
            <option value="size">容量</option>
            <option value="duration">長さ</option>
            <option value="title">タイトル</option>
          </select></label
        >
      </div>
      <label class="field">開始日（以降）<input v-model="ctx.values.from" lang="ja-JP" type="date" /> </label>
      <label class="field">終了日（まで）<input v-model="ctx.values.to" lang="ja-JP" type="date" /> </label>
      <label class="field"
        >最小サイズ（GB）<input v-model="ctx.values.min_size" type="number" min="0" step="0.1" />
      </label>
      <label class="field"
        >最大サイズ（GB）<input v-model="ctx.values.max_size" type="number" min="0" step="0.1" />
      </label>
      <label class="field"
        >保存先<select v-model="ctx.values.storage_id">
          <option :value="undefined">すべて</option>
          <option v-for="storage in ctx.storages" :key="storage.id" :value="storage.id">
            {{ storage.name }}
          </option>
        </select>
      </label>
      <label class="field"
        >保存期限<select v-model="ctx.values.permanent">
          <option :value="undefined">すべて</option>
          <option value="true">永久</option>
          <option value="false">期限あり</option>
        </select>
      </label>
      <label class="field"
        >保護理由<select v-model="ctx.values.reason">
          <option :value="undefined">すべて</option>
          <option value="REMOTE_PRIVATE">YouTube 側で非公開</option>
          <option value="REMOTE_DELETED">YouTube 側で削除</option>
          <option value="REMOTE_CONTENT_CHANGED">YouTube 側で編集</option>
          <option value="MANUAL">手動保護</option>
        </select>
      </label>
    </div>
    <div class="filter-chips">
      <span v-for="key in ctx.archiveFilterKeys" v-show="hasFilter(key)" :key="key" class="chip"
        >{{ filterName(key) }}: {{ filterValue(key) }}
        <button
          class="icon-button"
          style="height: 24px; min-height: 24px; width: 24px; font-size: 14px"
          :aria-label="`${key} 条件を解除`"
          @click="ctx.clearFilter(key)"
        >
          <Icon name="close" />
        </button>
      </span>
    </div>
    <div class="pager">
      <span>{{ ctx.pageStart }}–{{ ctx.pageEnd }} / 全 {{ ctx.total }} 件</span>
      <div class="actions">
        <button class="button secondary" :disabled="ctx.offset === 0" @click="ctx.page(-1)">前へ</button>
        <button
          class="button secondary"
          :disabled="ctx.offset + ctx.pageSize >= ctx.total"
          @click="ctx.page(1)"
        >
          次へ
        </button>
      </div>
    </div>
    <div v-if="ctx.loading && !ctx.videos.length" class="list">
      <div v-for="n in 4" :key="n" class="skeleton"></div>
    </div>
    <div v-else-if="!ctx.videos.length" class="empty-state">条件に一致する録画はありません。</div>
    <div class="list">
      <article v-for="video in ctx.videos" :key="video.id" class="card video-row">
        <div class="thumbnail-wrap">
          <img
            class="thumbnail"
            loading="lazy"
            decoding="async"
            :src="video.has_thumbnail ? ctx.apiUrl(`/videos/${video.id}/thumbnail`) : '/icon.svg'"
            :alt="video.title"
          />
          <span v-if="video.status === 'RECORDING'" class="live-label">LIVE</span>
          <span v-else-if="video.duration_seconds" class="duration-label">{{
            ctx.formatDuration(video.duration_seconds)
          }}</span>
        </div>
        <div class="item-main">
          <div class="archive-meta-line">
            <div class="item-title archive-title">{{ video.title }}</div>
            <span class="muted">{{ video.channel_name }} · {{ ctx.formatDate(video.recorded_at) }}</span>
            <span v-if="video.manual" class="badge info">手動追加</span>
            <div class="badge-row">
              <span
                v-for="badge in ctx.videoBadges(video)"
                :key="badge.label"
                class="badge"
                :class="badge.tone"
                :title="video.status"
                >{{ badge.label }}</span
              >
            </div>
            <span v-if="(video.recording_start_offset_ms || 0) > 0 && video.live_from_start !== 'used'"
              >冒頭 {{ Math.ceil((video.recording_start_offset_ms || 0) / 60_000) }} 分欠け</span
            >
            <span v-if="video.archive_backfill === 'queued'">アーカイブ補完中</span>
            <span
              >{{ ctx.formatBytes(video.file_size) }} · {{ video.storage_name || '保存先なし' }} ·
              {{ ctx.archiveRetentionLabel(video) }}</span
            >
          </div>
          <div v-if="video.status === 'RECORDING'" class="meta">
            <span>経過 {{ ctx.formatDuration(Number(ctx.metrics[video.id]?.elapsed_seconds)) }}</span>
            <span>書込 {{ ctx.formatBytes(Number(ctx.metrics[video.id]?.write_bytes_per_second)) }}/秒</span>
            <span>heartbeat {{ ctx.relativeTime(video.recording_heartbeat_at) }}</span>
          </div>
          <details v-if="video.last_error" class="inline-error">
            <summary>直近のエラー</summary>
            {{ video.last_error }}
          </details>
          <label v-if="ctx.conversionProgress[video.id]" class="meta conversion-progress">
            {{ ctx.conversionProgress[video.id].mode === 'remux' ? 'MP4 リマックス' : 'H.264 変換' }}
            {{ ctx.conversionProgress[video.id].progress }}%
            <progress :value="ctx.conversionProgress[video.id].progress" max="100"></progress>
          </label>
        </div>
        <div class="actions">
          <button
            v-if="video.status === 'RECORDING'"
            class="button danger small"
            :disabled="ctx.pendingStops.has(video.id)"
            @click="
              ctx.confirm(
                '録画を停止',
                `「${video.title}」の録画を停止します。現在までのファイルは保存されます。`,
                () => ctx.stopRecording(video),
              )
            "
          >
            {{ ctx.pendingStops.has(video.id) ? '停止中…' : '停止' }}
          </button>
          <button v-else-if="video.has_file" class="button primary small" @click="ctx.openVideo(video)">
            再生
          </button>
          <button
            v-else-if="video.live_playback_available"
            class="button primary small"
            @click="ctx.openVideo(video, true)"
          >
            視聴
          </button>
          <details class="menu">
            <summary class="button small" aria-label="動画操作"><Icon name="more" /></summary>
            <div class="menu-items">
              <a v-if="video.has_file" :href="ctx.apiUrl(`/videos/${video.id}/file?download=true`)"
                >ダウンロード</a
              >
              <button v-if="video.has_file" @click="ctx.convertVideo(video, 'remux')">MP4 リマックス</button>
              <button v-if="video.has_file" @click="ctx.convertVideo(video, 'h264')">H.264 変換</button>
              <button v-if="video.status !== 'RECORDING'" @click="ctx.toggleProtection(video)">
                {{ video.protected ? '保護を解除' : '永久保存にする' }}
              </button>
              <button
                v-if="['RECORDING_FAILED', 'WAITING_FOR_STORAGE'].includes(video.status)"
                @click="
                  ctx.api
                    .action(video.id, 'recording/retry')
                    .then(() => ctx.load(true))
                    .catch((e: unknown) =>
                      ctx.push(e instanceof Error ? e.message : '再試行できません', 'error'),
                    )
                "
              >
                再試行
              </button>
              <a
                :href="`https://www.youtube.com/watch?v=${video.youtube_video_id}`"
                target="_blank"
                rel="noreferrer"
                >YouTube で開く ↗</a
              >
              <button
                v-if="video.status !== 'RECORDING' && !ctx.scopeDeleted"
                class="destructive"
                @click="ctx.deleteVideo(video)"
              >
                削除
              </button>
            </div>
          </details>
        </div>
      </article>
    </div>
    <div class="pager">
      <span>{{ ctx.pageStart }}–{{ ctx.pageEnd }} / 全 {{ ctx.total }} 件</span>
      <div class="actions">
        <button class="button secondary" :disabled="ctx.offset === 0" @click="ctx.page(-1)">前へ</button>
        <button
          class="button secondary"
          :disabled="ctx.offset + ctx.pageSize >= ctx.total"
          @click="ctx.page(1)"
        >
          次へ
        </button>
      </div>
    </div>
  </section>
</template>
