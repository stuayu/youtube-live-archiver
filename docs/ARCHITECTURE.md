# YouTube Live Archiver アーキテクチャ

## 構成とモジュール

FastAPI と APScheduler を同一 backend プロセスで動かします。PostgreSQL はアプリ状態、ジョブ、監査履歴、チャット、fingerprint、文字起こしを保存します。Vue 3 / TypeScript の frontend は本番 build 時に FastAPI から配信します。

### Backend

| パス | 役割 |
| --- | --- |
| `app/main.py` | FastAPI lifespan、scheduler の起動・停止、health、静的 frontend 配信、エラー応答。 |
| `app/config.py` | `Settings` と環境変数既定値。 |
| `app/db.py` | SQLAlchemy engine / session、DB 接続設定と request 終了時の rollback / close。 |
| `app/models.py`, `app/schemas.py` | DB モデル、API 入出力 schema。 |
| `app/api/` | `common.py` の `/api` router を `__init__.py` で集約。`channels.py`、`storages.py`、`videos.py`、`settings.py`、`rules.py`、`system.py` が各 endpoint を提供します。 |
| `app/workers.py` | scheduler の監視、録画 dispatch、Retention、protection、復旧、整合性確認。 |
| `app/services/monitor.py` | チャンネルスキャン、初回 baseline、予定時刻更新、ポーリング間隔、エラー状態。 |
| `app/services/manual_video.py` | 手動動画 URL の安全な YouTube 動画 ID 解釈と正規 URL 生成。 |
| `app/services/chat.py` | チャット取り込み、リプレイ専用 worker、手動優先 queue、コメント画像処理。 |
| `app/services/image_cache.py` | 外部サムネイルの host 制限、取得、キャッシュ、失敗 cooldown。 |
| `app/services/recorder.py` | 録画枠、プロセス管理、yt-dlp / ffmpeg tee、chat subprocess、成果物救出、完了 commit。 |
| `app/services/recovery.py` | Remote 状態に基づく復旧判断、`.part` 救出、中間ファイル整理、stem 採番。 |
| `app/services/backfill.py` | `recording.json` と成果物を使う既存録画の補完・再構築処理。 |
| `app/services/locks.py` | PostgreSQL session advisory lock と処理別 lock namespace。 |
| `app/services/policy.py` | Retention、Remote 判定、削除許可の fail-closed policy。 |
| `app/services/fingerprints.py`, `quality.py`, `youtube.py`, `download.py`, `ffmpeg.py` | YouTube 取得、形式選択、ダウンロード、メディア解析。 |
| `app/services/chat.py`, `chat_assets.py` | ライブチャット解析・取得・再試行と絵文字画像キャッシュ。画像の配信（`/api/chat-assets/{key}`）は DB を読まず、ディスクのキャッシュと、チャット API が発行した key → URL の対応（プロセス内）だけで解決する。 |
| `app/services/live_playback.py` | 追っかけ HLS の生成、上限管理、安全な配信、後処理。 |
| `app/services/processing.py`, `transcription.py`, `summarization.py`, `search.py` | DB 処理キュー、文字起こし、任意要約、全文検索と timeline。 |
| `app/services/recording_rules.py`, `channel_stats.py`, `derived.py`, `transcode.py` | 録画ルール、統計、派生処理、変換。 |

公開 API の prefix は `/api` です。代表例: `/api/channels`、`/api/storages`、`/api/videos`、`/api/recording-rules`、`/api/settings/transcription`、`/api/settings/summary`、`/api/status`、`/api/events`、`/api/search/transcripts`、`/api/quality-presets`。health endpoint は `/health` です。完全な route 定義は `backend/app/api/` を参照してください。

### Frontend

- `src/views/`: Home / Channels / Upcoming / Archive / Search / Storages / Analysis / Activity / Settings の画面。
- `src/components/`: 共通ダイアログ、フォーム、録画行、DPlayer、プレイヤーパネル、設定セクション。
- `src/composables/`: API と画面状態を結ぶ workspace、各画面の query / fetch / action。
- `src/lib/`: API 表現補助、状態表示、format、player 設定、DPlayer adapter。
- `src/api/`: API client、resource 関数、型。

DPlayer の歯車メニューを拡張するときは `src/lib/dplayerSettings.ts` に項目と反映処理を追加し、設定の永続化・既定値は `src/lib/playerSettings.ts`、プレイヤーとの接続は `src/components/PlayerDialog.vue` に合わせます。DPlayer 内部 DOM を使う拡張は依存バージョンと unit test を合わせて確認します。

録画後の primary media を ffprobe し、結果を `raw_metadata.media_info` に保持します。
一覧は未解析レコードを1リクエスト最大5件まで、詳細は1件だけ解析します。
ffprobe 実行中に DB transaction を保持しません。Video API は `source` / `remux` /
`h264` rendition を返します。`/file?rendition=source|remux|h264` は指定ファイルだけを
配信し、未生成の派生物は404です。旧 `playable=true` は remux と同じ扱いです。

プレイヤー選択は `src/lib/playback.ts` の純粋関数で行います。
MP4 / WebM のブラウザ対応ソースを優先し、MKV は常に MP4 リマックスします。
リマックスも非対応なら利用者操作で H.264 + AAC に変換します。
変換は FFmpeg `-progress` を読み、再起動時に完成済み派生物を completed として返します。
変換元・配信・rendition・掃除は、いずれも `services/media.py` の `source_media()`
（`recording.json` の `primary_media`、無ければ最大のメディア、それも無ければ救出済みの
`.partial.mkv`）で同じファイルを選びます。変換の失敗時は ffmpeg のエラー出力を状態に残し、
アプリ終了時は実行中の変換プロセスを止めて待ち行列の変換を破棄します（一時ファイルは削除）。
画面側は、変換の状態が `idle` に戻った場合（backend の再起動など）に変換を開始し直します。
起動時に残存 `*.tmp.mp4` を消し、`PLAYABLE_CACHE_DAYS` のアクセス期限を過ぎた
派生物だけを監査ログ付きで掃除します。録画の file_size、容量、Retention、
整合性確認は元メディアだけを対象にします。読み込み失敗の判定は `<video>` のメタデータ読み込み（12秒）と `error` で行い、
再生位置の進行では判定しません（自動再生の拒否を失敗と誤認しないため）。
DPlayer の弾幕は、初期化オプションでは `danmaku.api`、`danmaku.reload()` では `address` に
エンドポイントを渡します（1.27.1 の実装に合わせています）。ライブ HLS は Safari のネイティブ経路と
他ブラウザの hls.js 経路を使います。E2E に WebKit project を含め、実行前に
`npx playwright install webkit` が必要です。

## Scheduler と監視

Scheduler は有効時、channel monitor / recorder を5秒間隔、recovery を1分間隔、
integrity を30分間隔、Retention を15分間隔、派生物掃除を24時間間隔、
protection を6時間間隔、processing queue を10秒間隔で登録します。
起動時に stale recording recovery も実行します。

監視は初回スキャンでチャンネル一覧を取得し、baseline を確定します。`next_check_at` が未設定または期限到来した有効チャンネルを batch 単位で処理します。`AUTO` は既定で、過去90日の開始時刻から UTC の時間帯（24区分）または曜日×時間帯（168区分）を学習し、hot / warm / cold / 履歴不足に応じて15〜30分 / 30〜60分 / 2〜4時間 / 30〜60分で確認します。開始時刻のある学習サンプルが12本未満なら、inspect 枠とクールダウンを確認し、スキャンごとに開始時刻不明の過去配信を1本だけ調べます。試行済み動画は `start_probe_attempted` で記録し再試行しません。固定値は30 / 45 / 60 / 90 / 120 / 180 / 240 / 360分の±10%です。次の hot 時間帯を越す場合は確認間隔を切り詰めます。`WAITING_LIVE` は予定の30分前までに確認し、30〜5分前は2〜4分、5分前〜10分後は20〜40秒、10〜60分後は1〜2分、1〜6時間後は5〜10分、時刻不明は10〜20分で確認します。予定による短縮は固定値でも有効です。スキャン失敗はチャンネルの `last_error` と `consecutive_failures` を更新し、指数 backoff を適用します。Rate limit はグローバル cooldown とし、連続時に10分から最大60分へ延長します。成功後に cooldown streak をリセットします。

システム API と画面は scheduler の job 実行時刻・失敗、監視中チャンネル数、チャンネルの前回成功・失敗、次回確認時刻を表示します。モニターの process 内 cooldown は複数 backend instance 間で共有されません。

`chat_replay_worker` は60秒間隔・1 instance で実行し、1 tick につき期限到来ジョブを1本取得します。中断ジョブをロック所有確認後に `retry_wait` へ戻し、10分ごとに録画済み・チャット有効・ファイルあり・削除前で未取得の動画を1 SQLで最大20件抽出します。自動ジョブは5分刻みで予約し、手動 queue は最優先です。取得中は同期DB transaction を保持せず、rate limit は monitor の共有 cooldown に反映します。文字起こし・要約 worker から独立しています。

`manual_video_worker` は 60 秒間隔で監視チャンネルに依存せず手動追加の `WAITING_LIVE` を確認し、開始・終了・非公開状態に応じて録画対象化または中止します。確認時刻は各動画の `manual_next_check_at` に記録し、アクセス制限時は monitor と共有する process 内 cooldown を適用します。

## 動画状態と遷移

`videos.status` の状態と主要遷移条件です。`is_partial`、`protected`、`raw_metadata` 内の制御情報は status と別の属性です。

| 状態 | 意味・主な遷移 |
| --- | --- |
| `WAITING_LIVE` | 予定配信を待機。開始・開始済み検出で `DISCOVERED`。取り消し・非公開等の確認で `CANCELLED`。 |
| `DISCOVERED` | 録画またはアーカイブ補完の queue。枠が空き保存先が用意できると `RECORDING`。アーカイブ補完中は `raw_metadata.archive_backfill="queued"`。 |
| `RECORDING` | live/archive subprocess 実行中。完了後 `ACTIVE` または保護状態。失敗・異常終了は `RECORDING_FAILED`。保存先が使用不能なら `WAITING_FOR_STORAGE`。 |
| `WAITING_FOR_STORAGE` | 保存先再試行待ち。再利用可能な storage が見つかれば `DISCOVERED` / `RECORDING` の処理へ戻る。 |
| `RECORDING_FAILED` | 停止・異常終了・出力や後処理失敗。部分成果物を保持し、復旧 worker が公開中なら live 再開、終了済みなら archive 取得を queue する。判定不能は次回確認時刻を設定。 |
| `ACTIVE` | 完全性確認を通った通常の保存済み録画。Retention 期限到来で `VERIFYING_REMOTE`。手動・自動保護で `PROTECTED`。 |
| `VERIFYING_REMOTE` | Retention 初回または再確認中。未編集・公開確認なら `DELETE_ELIGIBLE`、保護理由確定で `PROTECTED`、判定不能で `PROTECTION_PENDING`。 |
| `DELETE_ELIGIBLE` | 初回 Remote 判定成功後、削除猶予期間中。猶予満了後の独立した最終確認と削除条件が揃った場合のみ `DELETED_BY_RETENTION`。 |
| `PROTECTION_PENDING` | Remote / duration / fingerprint の判定不能。再確認対象として残し、再確認が成功したら猶予期間へ戻すか最終確認を完了する。判定不能のまま削除しない。 |
| `PROTECTED` | Remote 非公開・削除・利用不能・編集、手動保護、部分録画など。通常 Retention から除外。 |
| `SKIPPED` | 初回過去配信の上限、録画ルール、手動設定などで録画対象外。 |
| `CANCELLED` | 予定配信の取り消しまたは予定外終了を確認。再スキャンで upcoming / live と再認識されると `WAITING_LIVE` / `DISCOVERED` に戻る場合がある。 |
| `DELETED_BY_RETENTION` | Retention によるファイル削除完了。動画行と監査履歴を保持。 |
| `DELETED_MANUALLY` | UI の明示的な手動削除。監査履歴を保持。復元可能な trash 状態ではない。 |

`archive_backfill="queued"` は status 値ではありません。部分ライブの補完を `DISCOVERED` として recorder queue に戻し、archive profile の取得を開始します。完全と判定したとき `archive_backfill="completed"` とし、primary media を archive へ切り替えます。duration 差が tolerance を超える補完は incomplete と記録し、部分状態のまま再試行します。

`is_partial=true` は未完了 archive、欠けた先頭、ユーザー停止、複数 live 区間の未統合などを表します。部分録画は保持期限を付けず Retention 対象から除外します。救出可能な user stop は `raw_metadata.user_stopped=true` にし、自動 recovery worker は再開しません。規定回数を使い切ると `raw_metadata.recovery_exhausted=true` にして自動試行を止めます。これは `status` ではありません。

## 録画ディレクトリとファイル

標準 directory は `{storage_root}/{channel_name}/{YYYY-MM-DD title [video_id]}` です。日付は予定開始時刻、なければ行作成時刻、さらに無ければ現在時刻をサーバーのローカル日付にします。
チャンネル名とタイトルは Windows 向けに安全化して長さを制限します。既存の DB path が保存先配下で、フォルダー名が動画 ID に一致する場合は同じ directory を継続使用します。

yt-dlp output の stem は最初の live が `video`、後続 live が `video-part-2`、`video-part-3` …です。archive profile は live 区間が無ければ `video`、それ以外は `video-archive`、重複時は番号付き stem です。stem ごとに `*.info.json`、`*.description`、thumbnail、`*.live_chat.json`、media が生成されます。録画中の chat file は `.part` から final 名へ移します。

録画 directory 内の `recording.json` は動画 / チャンネル ID、開始終了時刻、duration、primary media、segment / interval、attempt outcome、profile、format selector / fallback を記録します。異常終了時は救出できた場合 `{stem}.partial.mkv` を作り、`recording.json` を成果物と試行内容で更新します。録画エラーのログは同じ親 directory に出します。`checksum.sha256` は確定 primary media の SHA-256 です。

中間ファイル整理は recorder completion の成功後だけ実行します。同一 stem に属する yt-dlp `.part` / `.ytdl` と `stem.fNNN.ext` 型断片が対象です。`validated=true`、完成 media の存在とサイズ>0が必要です。live chat と他 stem の成果物は除外します。失敗、停止、異常終了、DB commit 失敗で directory や証拠ファイルを消去しません。Retention 削除または明示的な手動削除がファイル削除経路です。

## Recorder、同時実行、排他

`MAX_CONCURRENT_LIVE_RECORDINGS` と `MAX_CONCURRENT_ARCHIVE_DOWNLOADS` は別枠です。Recorder は process 内の semaphore と video ごとの PostgreSQL advisory lock を使います。chat replay / live chat は別 yt-dlp process です。Recorder は heartbeat を更新し、保存失敗時は transaction を rollback して次の周期に再試行します。90秒以上 stale な `RECORDING` は、lock 取得後に行を読み直し、まだ stale の場合だけ `RECORDING_FAILED` として reconciliation します。

Lock key は namespace と ID を BLAKE2b 64-bit hash 化します。channel monitor は `channel-monitor:{channel_id}`、Recorder は `video-recorder:{youtube_video_id}`、Retention は `video-retention:{youtube_video_id}` を使います。再試行と手動削除も Recorder lock を取得し、row を読み直して録画状態との競合を防ぎます。PostgreSQL 以外、取得失敗、所有確認不能は fail closed で処理を飛ばします。session advisory lock は専用 connection を critical section 中保持し、transaction は query 後に commit します。

複数 backend instance では advisory lock が monitor / recorder / retention の重複処理を抑止します。ただし同時実行 semaphore、process 内 cooldown、process manager の子プロセス停止状態は instance 間共有ではありません。scheduler の active な複数 instance 運用や共通状態の調整は別途必要です。録画停止 API はその recorder process が起動した子 process のみ制御できます。

## DB transaction の原則

YouTube、yt-dlp、ffmpeg、ファイル I/O など遅い外部処理を待つ間に ORM transaction を保持しません。外部処理の前に commit し、戻った後に状態を再取得・更新して commit します。DB session は request / job 終了時に rollback（未完了時）して close します。PostgreSQL 接続には `DB_IDLE_IN_TRANSACTION_TIMEOUT_MS` を設定します。

録画完了は media を先にディスクへ確定し、後処理後に DB commit を試みます。完了メタデータ内の JSON 更新も明示して保存します。切断等の retryable commit error は bounded retry します。永続失敗でも成果物を消さず、次回 recovery と整合性確認に委ねます。Retention は Remote 判定前後で video row と lock を再確認し、削除可否を確定してからファイル削除し、成功後に status と audit を commit します。

## Retention と integrity

初回 retention check は `ACTIVE` 等から `VERIFYING_REMOTE` にし、YouTube visibility と編集検出を確認します。`availability` が無い、または空の応答は `UNKNOWN` として扱い、削除経路へ進めません。録画 recovery は削除を行わないため、取得自体が成功し `live_status` が認識できる場合に限り、availability が無くても再取得または配信待ちへ進めます。OFF は編集検出なし、Basic は duration、Standard は duration + 映像 pHash、Strong はそれらと音声 fingerprint です。Standard / Strong は動画の相対位置 5 / 20 / 40 / 60 / 80 / 95% を照合します。初回確認が公開・未変更なら `DELETE_ELIGIBLE` と `deletion_after` を設定します。猶予後、独立した最終成功確認が過去確認と合わせて揃い、全削除条件と lock ownership が確認された場合だけ削除します。

Remote エラー、形式不整合、duration 欠落、fingerprint 不足は `PROTECTION_PENDING` に保留します。部分録画、手動保護、保護 channel は Retention worker が飛ばします。integrity worker は `ACTIVE` を batch 処理し、storage root が利用可能な場合に primary media の存在・非ゼロサイズを確認します。欠落時は `MEDIA_MISSING` audit を記録して `RECORDING_FAILED` に戻し、recovery worker に渡します。完全性確認の失敗だけを削除根拠にしません。

Docker Compose の backend は `recorder_shutdown_timeout_seconds` より長い `stop_grace_period` を設定し、コンテナ終了時に録画停止と成果物整理を完了できる猶予を確保します。

## チャット、再生、派生処理

ライブチャットは別プロセスで JSONL を取得します。チャット種別は通常、superchat / supersticker、membership、gift purchase / received を保持します。message ID で冪等取り込みし、削除 action や正常に取得した完全 replay snapshot のみ論理削除に反映します。 replay 取得失敗は状態 `unavailable` とし、retry job を登録する場合があります。絵文字画像は `DATA_DIR/chat-assets` へ取得し、cache key でアプリ内配信します。

yt-dlp の chat offset は元配信開始基準です。`recording_start_offset_ms` を引いてローカル media 時刻にします。チャット replay 再取得後に元時刻を再基準化し、動画再生位置移動時は画面のチャット範囲を追従更新します。

追っかけ HLS は `live/<video_id>` 下で録画中に追記し、byte budget を超えると古い segment を整理します。DB の `live_hls_path` で登録された安全な path のみ配信します。playlist は no-store、segment は immutable cache です。アーカイブ成功検証後だけ sidecar を整理します。

文字起こしと要約は `processing_jobs` に入り、録画 status から独立しています。文字起こしは faster-whisper、要約は opt-in で OpenRouter を使います。全文検索は正規化文字起こし segment をページングし、PostgreSQL `pg_trgm` が使えれば検索候補に利用し、無い場合は ILIKE へ fallback します。

## 設定と運用上の境界

環境変数・既定値は `app/config.py` が唯一の定義元です。UI から保存する runtime 設定は DB `AppSetting` に保存します。保存パス Template、通知、復元できるゴミ箱、簡易認証は実装していません。API と UI に認証はなく、プライベートネットワーク内での利用を前提にしています。
