<script setup lang="ts">
import { reactive } from 'vue'
import { useWorkspaceContext } from '../composables/workspaceContext'
const ctx = reactive(useWorkspaceContext())
</script>
<template>
  <section>
    <header class="page-head">
      <div>
        <h1>システム概要</h1>
        <p>自動収集の状態と、対応が必要な項目</p>
      </div>
    </header>
    <div v-if="ctx.status?.attention.length" class="attention-list card">
      <h2>要対応</h2>
      <div
        v-for="item in ctx.status.attention"
        :key="item.code + item.message"
        class="attention"
        :class="{ error: item.level === 'error' }"
      >
        <button class="text-link" @click="ctx.openAttention(item.code)">{{ item.message }}</button>
      </div>
    </div>
    <div v-else class="attention card">
      <b>問題はありません</b>
      <span> 監視と保存先は正常に動作しています。</span>
    </div>
    <div class="grid stats-grid">
      <button class="card stat-tile" @click="ctx.goToArchive('RECORDING')">
        <span class="muted">録画中</span>
        <strong>{{ ctx.status?.counts.RECORDING ?? 0 }}</strong>
      </button>
      <button class="card stat-tile" @click="ctx.setTab('upcoming')">
        <span class="muted">配信予定</span>
        <strong>{{ ctx.status?.counts.WAITING_LIVE ?? ctx.upcoming.length }}</strong>
      </button>
      <button class="card stat-tile" @click="ctx.goToArchive('ACTIVE')">
        <span class="muted">録画済み</span>
        <strong>{{ ctx.status?.counts.ACTIVE ?? 0 }}</strong>
      </button>
      <button class="card stat-tile" @click="ctx.goToArchive('RECORDING_FAILED')">
        <span class="muted">失敗・保留</span>
        <strong>{{
          (ctx.status?.counts.RECORDING_FAILED ?? 0) + (ctx.status?.counts.WAITING_FOR_STORAGE ?? 0)
        }}</strong>
      </button>
      <button class="card stat-tile" @click="ctx.goToArchive('PROTECTED')">
        <span class="muted">保護済み</span>
        <strong>{{ ctx.status?.counts.PROTECTED ?? 0 }}</strong>
      </button>
    </div>
    <div class="grid" style="grid-template-columns: repeat(auto-fit, minmax(min(100%, 360px), 1fr))">
      <section class="card">
        <div class="card-header">
          <h2>録画中</h2>
          <span class="badge danger">{{ ctx.status?.recordings.count ?? 0 }} 件</span>
        </div>
        <div v-if="ctx.recordings.length" class="list">
          <article v-for="recording in ctx.recordings" :key="recording.video_id" class="card">
            <b>{{ recording.title }}</b>
            <div class="meta">
              <span>{{ recording.channel_name }}</span>
              <span>開始 {{ ctx.formatDate(recording.started_at) }}</span>
              <span>heartbeat {{ ctx.relativeTime(recording.heartbeat_at) }}</span>
              <span v-if="ctx.metrics[recording.video_id]?.file_size">{{
                ctx.formatBytes(Number(ctx.metrics[recording.video_id]?.file_size))
              }}</span>
            </div>
            <div class="actions" style="margin-top: 8px">
              <button
                class="button danger small"
                :disabled="ctx.pendingStops.has(recording.video_id)"
                @click="
                  ctx.confirm(
                    '録画を停止',
                    `「${recording.title}」の録画を停止します。現在までのファイルは保存されます。`,
                    () => ctx.stopRecording({ id: recording.video_id }),
                  )
                "
              >
                {{ ctx.pendingStops.has(recording.video_id) ? '停止中…' : '停止' }}
              </button>
              <button
                v-if="ctx.channels.some((c) => c.name === recording.channel_name && c.live_playback_enabled)"
                class="button secondary small"
                @click="ctx.api.video(recording.video_id).then((v) => ctx.openVideo(v, true))"
              >
                視聴
              </button>
            </div>
          </article>
        </div>
        <p v-else class="empty-state">録画中の配信はありません。</p>
      </section>
      <section class="card">
        <div class="card-header">
          <h2>まもなく配信</h2>
          <button class="button secondary small" @click="ctx.setTab('upcoming')">すべて表示</button>
        </div>
        <div v-for="video in ctx.homeUpcoming" :key="video.id" class="transcript-row">
          <b>{{ video.title }}</b>
          <div class="meta">
            <span>{{ video.channel_name }}</span>
            <span>{{ ctx.upcomingStartLabel(video) }}</span>
          </div>
        </div>
        <p v-if="!ctx.homeUpcoming.length" class="empty-state">これから始まる配信予定はありません。</p>
      </section>
      <section class="card">
        <h2>監視</h2>
        <div class="meta">
          <span>有効チャンネル {{ ctx.status?.monitor.enabled_channel_count ?? 0 }}</span>
          <span>エラー中 {{ ctx.status?.monitor.error_channel_count ?? 0 }}</span>
          <span v-if="ctx.status?.monitor.cooldown_remaining_seconds"
            >クールダウン中・残り
            {{ ctx.formatDuration(ctx.status.monitor.cooldown_remaining_seconds) }}</span
          >
        </div>
        <div
          v-for="channel in ctx.channels.filter((c) => c.monitor_state === 'error')"
          :key="channel.id"
          class="transcript-row"
        >
          <b>{{ channel.name }}</b>
          <p class="inline-error">{{ ctx.monitorErrorSummary(channel.last_error) }}</p>
          <details v-if="channel.last_error">
            <summary>詳細</summary>
            {{ channel.last_error }}
          </details>
          <small
            >連続失敗 {{ channel.consecutive_failures }} 回 · 次回
            {{ ctx.relativeTime(channel.next_check_at) }}</small
          >
          <button class="button secondary small" @click="ctx.checkChannel(channel)">今すぐ確認</button>
        </div>
      </section>
      <section class="card">
        <div class="card-header">
          <h2>保存先</h2>
          <button class="button secondary small" @click="ctx.setTab('storages')">管理</button>
        </div>
        <div v-for="item in ctx.usage" :key="item.id" style="margin: 12px 0">
          <div class="card-header">
            <b>{{ item.name }}</b>
            <span class="badge" :class="item.reachable && item.writable ? 'success' : 'danger'">{{
              item.reachable ? (item.writable ? '書込可能' : '書込不可') : '到達不能'
            }}</span>
          </div>
          <div class="progress">
            <i
              :style="{
                width: item.total_bytes
                  ? `${Math.min(100, ((item.used_bytes ?? 0) / item.total_bytes) * 100)}%`
                  : '0%',
              }"
            >
            </i>
          </div>
          <div class="meta">
            使用 {{ ctx.formatBytes(item.used_bytes) }} · 空き {{ ctx.formatBytes(item.free_bytes) }} ·
            本アプリ {{ ctx.formatBytes(item.app_bytes) }}
          </div>
        </div>
        <p v-if="!ctx.usage.length" class="empty-state">
          保存先がありません。<button class="button primary" @click="ctx.setTab('storages')">
            保存先を登録
          </button>
        </p>
      </section>
      <section class="card">
        <div class="card-header">
          <h2>最近のイベント</h2>
          <button class="button secondary small" @click="ctx.setTab('activity')">アクティビティへ</button>
        </div>
        <div v-for="event in ctx.latestEvents" :key="event.id" class="event-row">
          <time>{{ ctx.formatDate(event.created_at) }}</time>
          <div>
            {{ ctx.labelForEvent(event) }}<small v-if="event.video_title">{{ event.video_title }}</small>
          </div>
        </div>
        <p v-if="!ctx.events.length" class="empty-state">イベントはありません。</p>
      </section>
    </div>
    <details id="system-details" class="card">
      <summary>システム詳細</summary>
      <div class="meta">
        <span>DB {{ ctx.status?.database.available ? '接続中' : '未接続' }}</span>
        <span
          >DB モード
          {{ ctx.status?.database.mode === 'embedded' ? '組み込み PostgreSQL' : '外部 PostgreSQL' }}</span
        >
        <span v-if="ctx.status?.database.data_dir">DB データ {{ ctx.status.database.data_dir }}</span>
        <span>スケジューラー {{ ctx.status?.scheduler.running ? '稼働中' : '停止中' }}</span>
        <span>
          YouTube IP {{ ctx.status?.youtube_ip?.mode ?? '不明' }} ·
          {{ ctx.status?.youtube_ip?.address_family ?? '不明' }}
        </span>
        <span v-if="ctx.status?.youtube_ip?.checked_at">
          IPv6 到達性 {{ ctx.status.youtube_ip.ipv6_reachable ? 'あり' : 'なし' }} ·
          {{ ctx.formatDate(ctx.status.youtube_ip.checked_at) }}
        </span>
        <span v-else-if="ctx.status?.youtube_ip?.mode === 'prefer_ipv6'"> IPv6 到達性を確認中 </span>
        <span>yt-dlp {{ String(ctx.status?.tools.yt_dlp_version ?? '不明') }}</span>
        <span v-if="ctx.status?.tools.yt_dlp_latest_version">
          最新 {{ String(ctx.status.tools.yt_dlp_latest_version) }}
        </span>
        <span v-if="ctx.status?.tools.yt_dlp_update_available === true" class="error-text">
          新しい yt-dlp 版あり{{ ctx.status.tools.yt_dlp_update_overdue ? '・7日以上経過' : '' }}
        </span>
        <span v-else-if="ctx.status?.tools.yt_dlp_latest_check_failed === true">
          yt-dlp 最新版を確認できず
        </span>
        <span v-if="ctx.status?.tools.yt_dlp_checked_at">
          版確認 {{ ctx.formatDate(String(ctx.status.tools.yt_dlp_checked_at)) }}
        </span>
        <span v-if="ctx.status?.tools.yt_dlp_release_date">
          リリース {{ ctx.formatDate(String(ctx.status.tools.yt_dlp_release_date)) }}
        </span>
        <span v-if="ctx.status?.tools.yt_dlp_auto_update">yt-dlp 自動更新: 有効</span>
        <span v-if="ctx.status?.tools.yt_dlp_update_error" class="error-text">
          yt-dlp 更新エラー: {{ String(ctx.status.tools.yt_dlp_update_error) }}
        </span>
        <span v-if="ctx.status?.tools.yt_dlp_update_in_progress">yt-dlp 更新中</span>
        <span>ffmpeg {{ ctx.status?.tools.ffmpeg_available ? '利用可能' : '未検出' }}</span>
      </div>
      <div v-for="job in ctx.status?.scheduler.jobs ?? []" :key="job.id" class="event-row">
        <b>{{ job.name }}</b>
        <span
          >次回 {{ ctx.formatDate(job.next_run_at) }} · 成功 {{ ctx.formatDate(job.last_success_at) }} · 遅延
          {{ job.last_delay_ms ?? 0 }} ms {{ job.last_error || '' }}</span
        >
      </div>
    </details>
  </section>
</template>
