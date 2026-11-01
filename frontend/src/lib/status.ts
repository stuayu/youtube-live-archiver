export interface StatusPresentation {
  label: string
  tone: 'neutral' | 'success' | 'info' | 'warning' | 'danger' | 'protected'
  description: string
}
// prettier-ignore
const videoStatuses: Record<string, StatusPresentation> = {
  WAITING_LIVE: { label: '配信待ち', tone: 'info', description: '配信開始を待っています' },
  DISCOVERED: { label: '録画待ち', tone: 'warning', description: '録画処理を待っています' },
  RECORDING: { label: '録画中', tone: 'danger', description: '配信を録画しています' },
  ACTIVE: { label: '録画済み', tone: 'success', description: '録画ファイルを保存しています' },
  RECORDING_FAILED: { label: '録画失敗', tone: 'danger', description: '録画に失敗しました' },
  WAITING_FOR_STORAGE: { label: '保存先待ち', tone: 'warning', description: '利用可能な保存先を待っています' },
  VERIFYING_REMOTE: { label: '確認中', tone: 'warning', description: 'YouTube 側の状態を確認しています' },
  DELETE_ELIGIBLE: { label: '削除候補', tone: 'warning', description: '削除猶予期間中です' },
  PROTECTION_PENDING: { label: '確認保留', tone: 'warning', description: '安全確認のため保留しています' },
  PROTECTED: { label: '保護済み', tone: 'protected', description: '自動削除されません' },
  SKIPPED: { label: 'スキップ', tone: 'neutral', description: '録画対象から除外しました' },
  CANCELLED: { label: '中止', tone: 'neutral', description: '録画を中止しました' },
  DELETED_BY_RETENTION: { label: '自動削除済み', tone: 'neutral', description: '保存期間の終了後に削除しました' },
  DELETED_MANUALLY: { label: '手動削除済み', tone: 'neutral', description: '手動で削除しました' },
}
const reasons: Record<string, string> = {
  REMOTE_PRIVATE: 'YouTube 側で非公開',
  REMOTE_DELETED: 'YouTube 側で削除',
  REMOTE_UNAVAILABLE: 'YouTube 側で視聴不可',
  REMOTE_CONTENT_CHANGED: 'YouTube 側で編集',
  MANUAL: '手動保護',
  UNKNOWN: '理由不明',
}
const recordFailures: Record<string, string> = {
  RATE_LIMITED: 'YouTube からアクセス制限を受けています',
  PRIVATE: '非公開のため録画できません',
  DELETED: '動画が削除されています',
  MEMBERS_ONLY: 'メンバー限定のため録画できません',
  UNAVAILABLE: '動画を視聴できない状態です',
  NO_STREAMS_TAB: 'チャンネルの配信一覧を取得できません',
  ATTEMPT_FAILED: '録画処理が失敗しました',
  ATTEMPT_ERROR: '録画処理でエラーが発生しました',
  MISSING_MEDIA: '録画ファイルが作成されませんでした',
  INTERRUPTED_RECOVERY: '配信中に録画が中断しました',
  USER_STOP_EMPTY: '停止時に録画データがありませんでした',
  USER_STOP_NOT_SALVAGED: '停止時に録画を保存できませんでした',
  RECORDER_CRASH: '録画処理が繰り返し異常終了したため停止しました。ログを確認して手動で再試行してください',
}
const monitorErrors: Record<string, string> = {
  RATE_LIMITED: 'YouTube からアクセス制限を受けています',
  UNKNOWN: '取得に失敗しました（通信エラーなど）',
  PRIVATE: 'チャンネルを確認できません（非公開）',
  AUTHENTICATION: '認証が必要です。Cookie Profile を確認してください',
  NETWORK: '通信に失敗しました。接続状態を確認してください',
}
export function videoStatus(value: string): StatusPresentation {
  return (
    videoStatuses[value] ?? { label: value || '状態不明', tone: 'neutral', description: value || '状態不明' }
  )
}
export function protectionReason(value: string | null | undefined): string {
  return value ? (reasons[value] ?? value) : ''
}
export function recordFailureReason(value: string | null | undefined): string {
  return value ? (recordFailures[value] ?? value) : '理由不明'
}
export function monitorStatus(value: string): StatusPresentation {
  const states: Record<string, StatusPresentation> = {
    paused: { label: '停止中', tone: 'neutral', description: '監視を停止しています' },
    recording: { label: '録画中', tone: 'danger', description: 'チャンネルで録画中です' },
    error: { label: 'エラー', tone: 'danger', description: '監視でエラーが発生しています' },
    pending: { label: '確認待ち', tone: 'warning', description: '初回確認を待っています' },
    ok: { label: '監視中', tone: 'success', description: '定期監視中です' },
  }
  return states[value] ?? { label: value || '状態不明', tone: 'neutral', description: value || '状態不明' }
}
export function monitorErrorSummary(value: string | null | undefined): string {
  if (!value) return '監視エラーの詳細はありません'
  const code = value.split(':', 1)[0]?.trim() ?? ''
  return monitorErrors[code] ?? '監視情報の取得に失敗しました'
}

type ProcessingTarget = {
  recording_finished_at?: string | null
  transcription_status?: string | null
  transcription_error?: string | null
  summary_status?: string | null
  summary_error?: string | null
  summary_progress?: { done: number; total: number } | null
}

/** One compact badge for the transcript → summary pipeline, or null when there is nothing to show. */
export function processingStatus(video: ProcessingTarget): StatusPresentation | null {
  // 状態の初期値が pending なので、録画が完了した動画にだけ出す（配信予定・録画中・失敗は対象外）。
  if (!video.recording_finished_at) return null
  const transcription = video.transcription_status || 'disabled'
  const transcriptionStates: Record<string, StatusPresentation> = {
    pending: { label: '文字起こし待ち', tone: 'neutral', description: '文字起こしの順番を待っています' },
    processing: { label: '文字起こし中', tone: 'info', description: '文字起こしを実行しています' },
    retry_wait: {
      label: '文字起こし再試行待ち',
      tone: 'warning',
      description: video.transcription_error || '失敗したため、時間をおいて再試行します',
    },
    failed: {
      label: '文字起こし失敗',
      tone: 'danger',
      description: video.transcription_error || '文字起こしに失敗しました',
    },
  }
  if (transcription !== 'completed') return transcriptionStates[transcription] ?? null
  const progress = video.summary_progress
  // 区間要約が全部済んだら最後のまとめ段階。
  const step = progress
    ? progress.done >= progress.total
      ? '（まとめ）'
      : ` ${progress.done}/${progress.total}`
    : ''
  const stepNote = progress ? `（区間要約 ${progress.done}/${progress.total} 済み）` : ''
  const summaryStates: Record<string, StatusPresentation> = {
    pending: {
      label: `要約待ち${step}`,
      tone: 'neutral',
      description: `文字起こし済み。要約の順番を待っています${stepNote}`,
    },
    processing: {
      label: `要約中${step}`,
      tone: 'info',
      description: `外部プロバイダーで要約しています${stepNote}`,
    },
    retry_wait: {
      label: `要約再試行待ち${step}`,
      tone: 'warning',
      description: video.summary_error || 'プロバイダーの制限などで、時間をおいて再試行します',
    },
    failed: { label: '要約失敗', tone: 'danger', description: video.summary_error || '要約に失敗しました' },
    completed: { label: '要約済み', tone: 'success', description: '文字起こしと要約が完了しています' },
  }
  return (
    summaryStates[video.summary_status || 'disabled'] ?? {
      label: '文字起こし済み',
      tone: 'success',
      description: '文字起こしが完了しています（要約は無効または未設定）',
    }
  )
}
