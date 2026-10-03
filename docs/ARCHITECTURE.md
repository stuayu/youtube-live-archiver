# YouTube Live Archiver アーキテクチャ

## 目的と安全原則

期限後の動画は YouTube の状態・編集状態・確認履歴を調べ、**削除してよいと確認できた動画だけ**削除する。Remote 問い合わせ、duration、fingerprint、ストレージ、ファイル削除のどれかが失敗または不明なら KEEP / `PROTECTION_PENDING` とする。

## モジュール

- `app/main.py`: FastAPI 起動と単一プロセス APScheduler 起動。
- `app/models.py`: SQLAlchemy モデル。videos / channels / channel_stats / storages / remote_checks / fingerprints / audit_logs。
- `app/api.py`, `app/schemas.py`: 設定 CRUD、動画保護、録画停止・再試行 API。
- `app/services/youtube.py`: yt-dlp を使うメタデータ/チャンネル一覧取得。Cookie ファイルまたは browser profile を適用。
- `app/services/backfill.py`: 過去配信の件数上限、Range 構文、登録 Storage 配下パスの検証。
- `app/services/transcode.py`: API 要求から MP4 リマックス / H.264 + AAC 変換を単一 worker キューで実行する。出力は一時ファイルから atomic replace し、ソース録画を変更しない。同じ video / mode の二重投入を抑止する。
- `app/services/download.py`: 静的取得で aria2c を選択し、実行ファイルが PATH に無ければ native downloader に戻す。ライブ録画では常に native。
- `app/workers.py`: ストレージ書込・空き容量チェック、録画、完了後の duration と fingerprint 記録。
- `app/services/fingerprints.py`: ffmpeg の映像 perceptual hash と音声特徴サンプル生成。
- `app/services/policy.py`: Remote 分類、fingerprint 比較、Retention 条件の副作用なし判定。
- `app/services/locks.py`: PostgreSQL session advisory lock と名前空間付きキー生成。
- `app/workers.py`: チャンネル監視、録画、Retention、Protection のジョブ。
- `frontend/`: Vue 3 設定・アーカイブ GUI。
- `alembic/`: PostgreSQL のスキーマ migration。

## API 表現

公開 JSON の ID は文字列で表し、DB の整数主キーとは境界で変換する。GET コレクションはステータス集計などを除き配列を返す。日時は UTC の ISO 8601（`+00:00` または `Z`）とし、HTTP / 入力検証エラーは `{"error":"..."}` 形式へ揃える。内部 DB ID を整数のまま保つため、数値キーを含む入力は API 境界で受け付けて検証する。

## チャンネル分析

チャンネルメタデータ取得が成功したとき、前回の channel_stats 記録から `CHANNEL_STATS_INTERVAL_HOURS`（既定24時間）以上経過していれば現在値を1件保存する。取得に失敗した場合は行を追加しない。subscriber_count は取得した整数値を保持し、YouTube の表示値が丸められている可能性を `subscriber_count_rounded` に併記する。channel_stats は 0006 migration で作成する。

`GET /api/channels/{id}/stats` は期間内に実在する標本を day/week/month ごとに集約する。欠測期間を補間しない。比較用の前日・前週・前月値は期間開始前の標本も参照し、対象時点以前で最も新しい標本が存在しない場合は差分を null にする。録画指標は選択期間内に完了した録画レコードから計算する。GUI の折れ線は SVG を使い、欠測日を跨いで線を結ばない。

## ルール録画

`recording_rules` はタイトルまたは `raw_metadata.description` に対するキーワード/正規表現、大小文字区別、全体/特定チャンネル、有効状態、優先度を保存する。チャンネル監視で追加された動画を Recorder が取得する前に評価する。除外ルールの一致は常に録画を止める。有効な包含ルールが1つ以上ある場合、そのチャンネルに適用される包含ルールのどれにも一致しない動画は `SKIPPED` にする。包含ルールが無い場合は従来どおり全候補を処理する。

一致した最優先ルールは保存先・保存期間・画質 selector・編集検出モードを動画にスナップショットする。保存先が利用できない場合はチャンネルの fallback / default へ進む。`POST /api/recording-rules/preview` は既存 videos のタイトルと保存済み description を照合し件数と先頭20件を返す。既存動画に説明文が保存されていない場合、プレビューはタイトルと空の説明文で評価する。ルール削除は将来の候補だけに影響し、録画済み動画の設定は変えない。

Retention の自動削除条件はルールから変更できない。失敗・保留・部分録画をルールが削除可能状態へ遷移させることはない。

## GUI 用 metadata とアーカイブ配信

チャンネル登録時に yt-dlp から表示名・アイコン等を取得する。取得または画像キャッシュに失敗しても登録を成功させ、「情報更新」で再取得できる。アイコン画像は `DATA_DIR/channel-icons` に格納する。UPCOMING は `WAITING_LIVE` とし、`scheduled_start_at` と thumbnail URL を保存して開始予定順に表示する。過去配信は新しい順に `backfill_limit` 本だけ `DISCOVERED` にし、それ以外は `SKIPPED` として録画対象から除外する（既定0）。

動画一覧 API はページングし、再生・ダウンロード API は video ID の DB 登録パスのみを解決する。保存先 Storage のルート配下にある録画ディレクトリとメディアファイルをサーバー側で検証する。再生は単一 byte Range を返す。MKV はオンデマンドで MP4 にリマックスでき、H.264 + AAC は明示要求時だけ変換キューへ入る。どちらも元ファイルを保持する。手動削除は明示操作を audit log に記録する。Retention Worker の条件には変更を加えない。

## UPCOMING / チャンネル監視

手動削除は現状、明示確認後にローカル録画ディレクトリを削除する操作で、復元可能なゴミ箱はまだない。Retention の自動削除は別の Worker と要件30章の Remote / Grace 判定を通り、手動削除の確認状態を流用しない。ゴミ箱を追加する場合も、復元用の論理削除状態を Retention の `DELETE_ELIGIBLE` / `DELETED_BY_RETENTION` と分離する。

監視ジョブは5秒ごとに動作し、チャンネルごとの `next_check_at` を過ぎたものだけ取得する。初回にランダム位相を設定し、通常間隔にも jitter を加える。AUTO は通常30〜60分、Upcoming は予定時刻までの距離に応じて5〜10分 / 1〜3分 / 15〜30秒へ短縮する。開始・終了状態を検知したら video を録画キューへ移し、別ポーラーで同じ動画を高頻度確認しない。

## 録画とストレージ

Recorder は Primary、Fallback、Default の順に有効なストレージを検査し、存在・書込・空き容量を確認する。どれも使えなければ `WAITING_FOR_STORAGE`。yt-dlp の正常終了、メディアファイルの存在、metadata 処理と要求 fingerprint 処理がすべて完了してから `ACTIVE` にする。異常終了・停止・後処理失敗では `RECORDING_FAILED` とし、部分ディレクトリを消去する。録画停止は単一 backend プロセス内の子プロセスに terminate を送る。`RECORDING` 中は video ID の Recorder lock を保持し、20秒ごとに `recording_heartbeat_at` を更新する。

## 排他制御とクラッシュ復旧

ジョブ間の PostgreSQL session advisory lock は `lock_id(namespace, id)` が生成する符号付き64bit BLAKE2b hash を使う。名前空間とIDを一箇所で結合して hash 化し、用途ごとに次の名前空間を分離する。

| 処理 | lock 単位 | 名前空間 |
| --- | --- | --- |
| Channel Monitor / Live Detector | YouTube channel ID | `channel-monitor` |
| Recorder | YouTube video ID | `video-recorder` |
| Retention / Protection | YouTube video ID | `video-retention` |

同一IDでも名前空間が異なれば別 lock になる。lock 取得失敗または PostgreSQL 以外の DB では対象をスキップする。保持処理では lock 保有を削除直前に再確認し、確認不能・喪失時はファイルを削除しない。監視と録画も処理確定前に lock 保有を確認する。

Recorder lock は録画時間中ずっと保持する PostgreSQL **session** advisory lock である。backend がクラッシュしてDB接続が切れると PostgreSQL が lock を自動解放するため、永続的なロック残留はない。接続上の確認クエリは都度 commit し、idle transaction を録画時間中保持しない。90秒以上 heartbeat が更新されていない行は lock を取得できた場合に `RECORDING_FAILED` に戻す。起動時と毎分の recovery worker は失敗・stale・成果物欠落を YouTube と照合する。公開中のライブなら別パート、公開アーカイブなら再取得をキューに入れる。非公開・削除済み・視聴不能なら部分成果物を削除せず保護し、Remote 判定不能なら指数バックオフで保留する。`recovery_attempts` と `next_recovery_at` を保存し、再取得不能時もローカルファイルを保持する。

Cookie Profile は yt-dlp の録画、metadata、channel scan、Remote sample download に適用する。Docker 環境ではファイルを backend にマウントする。browser profile はコンテナ内にある場合だけ利用できる。

## Downloader 選択

終了済みアーカイブ、Remote fingerprint の短い section、metadata 等の静的取得は `DOWNLOADER=aria2c` のとき aria2c を external downloader として使用する。`ARIA2C_PATH` が PATH 上で解決できない場合は downloader 引数を付けず yt-dlp native にフォールバックする。ライブ録画中 (`is_live`) の HLS は実時間のストリームであり、aria2c の分割並列ダウンロードが適さないため常に native を選ぶ。録画と Retention の安全判定は downloader の選択結果に依存せず、取得失敗は従来どおり失敗/`PROTECTION_PENDING` として扱い、自動削除を許可しない。

## Fingerprint / Remote 判定

録画完了後、5/20/40/60/80/95%地点で映像 pHash を記録し、Strong では音声特徴指紋も記録する。Retention 確認では YouTube archive の各相対位置を yt-dlp section download し、同じ抽出器で比較する。pHash と音声の Hamming 距離が設定しきい値を超えた場合 `REMOTE_CONTENT_CHANGED`、サンプル欠落・形式不整合・取得失敗の場合 `PROTECTION_PENDING`。

| モード | 判定に使う値 |
| --- | --- |
| OFF | 編集検出なし |
| Basic | duration |
| Standard | duration + 映像 fingerprint |
| Strong | duration + 映像・音声 fingerprint |

duration 許容値は `DURATION_TOLERANCE_SECONDS`、fingerprint 距離は `FINGERPRINT_VIDEO_THRESHOLD` / `FINGERPRINT_AUDIO_THRESHOLD` で設定する。メタデータ変更は映像編集とみなさない。

## Retention 二段階確認

```text
ACTIVE (期限到来)
 -> VERIFYING_REMOTE
 -> PUBLIC + UNCHANGED -> DELETE_ELIGIBLE + deletion_after
 -> 猶予満了後に再確認
 -> PUBLIC + UNCHANGED + 過去の成功確認あり -> ファイル削除 -> DELETED_BY_RETENTION
```

Private / deleted / unavailable / content changed は保護する。Remote エラー、duration 欠落、fingerprint 失敗は `PROTECTION_PENDING` とし、成功扱いの確認履歴に数えない。`may_delete` は期限・猶予・状態・Remote 成功・Public・未変更・未保護をすべて要求し、呼出側でも以前の成功確認を検証する。ファイル削除に失敗した場合、動画行を削除済みにしない。削除・保護は audit_logs に記録する。

## 運用上の制限

録画停止 API による子プロセス制御は、その Recorder を起動した backend プロセス内に限られる。Remote section download は配信形式や YouTube 側制限によって失敗し得るが、その場合は必ず削除保留へ倒す。
# 画質プロファイル

ライブ実時間録画と終了済みアーカイブ取得は別の `live_quality` / `archive_quality` を使う。ライブ既定値は空文字で yt-dlp の `-f` を省略し、アーカイブ既定値は AV1 → VP9 → best の format selector とする。チャンネル値は即時に反映し、開始時に解決した値と fallback の有無を videos にスナップショット保存する。動画個別・ルール指定を実装する場合も同じ resolver の先行優先値に追加する。利用不能 selector は自動選択で再試行し、結果をログと DB に記録する。
# Failure artifacts and deletion safety

Recording failure, a stale heartbeat, a non-zero recorder exit, or missing output
is never evidence that the destination can be deleted. Preserve the directory,
partial media, metadata, thumbnail, and an error log in the parent directory so
they remain inspectable and recoverable. Files may be removed only after the
retention policy has positively confirmed every deletion condition (REQUIREMENTS
chapter 30), or after an explicit user deletion action.

## ライブチャット

チャンネルの `chat_enabled`（既定 true）が有効な場合、録画時に yt-dlp の `--write-subs --sub-langs live_chat --sub-format json` を指定する。録画ファイルの成功判定とは独立して扱い、チャットファイルが無い/壊れている場合も録画は成功のままにする。完了済み動画の `/api/videos/{id}/chat/retrieve` は yt-dlp の成功と JSONL の解析完了後にのみ取り込みをコミットする。失敗時は保存済みコメントに触れない。

`chat_messages.message_id` は一意で再取り込みを冪等にする。ライブストリーム内の削除 action と、成功した完全な replay snapshot に限る「既存にあって再取得に無い」比較は論理削除を記録する。欠落時の source は `判定不能` とし、物理削除は行わない。

yt-dlp の `videoOffsetTimeMsec` はライブ開始を0としたオフセットである。`recorded_at` と metadata の `release_timestamp` の差を `recording_start_offset_ms` として保存し、ローカル再生時刻は `max(0, offset_ms - recording_start_offset_ms) / 1000` とする。timestamp 不明時は補正0。

## 録画中の追っかけ再生

チャンネル単位の `live_playback_enabled` は既定 OFF。ON のライブ録画だけ yt-dlp の media stdout を ffmpeg tee に渡し、同じ stream copy を保存用 MKV と `live/<video_id>/index.m3u8` 以下の HLS に分岐する。フォーマット selector、cookie、yt-dlp の URL 更新能力は従来録画と共有し、保存用ファイルは引き続き別の動画ディレクトリに置く。tee の起動・実行が失敗したら HLS を破棄し、失敗を audit log に記録して従来の yt-dlp 直書きを再起動する。

HLS ディレクトリは video DB 行の `live_hls_path` に登録された場所だけ API から配信する。ファイル名は限定し、playlist は no-store、segment は immutable cache とする。playlist は6秒 segment を追記し、チャンネル設定の byte 上限に達した場合は古い segment と playlist 行をローリング削除する。HLS は retention 対象外。録画成功後に保存 MKV が存在し、非ゼロで、ffprobe が正の duration を返した場合だけ HLS sidecar を削除する。検証に失敗または不明なら保持し、保存用 MKV は sidecar cleanup から削除しない。

DPlayer と hls.js を npm dependency として同梱し、DPlayer の danmaku API に保存コメントを渡す。完了済み録画は保存メディア、録画中は HLS を source に選び、hls.js の custom type で再生する。GUI ではライブエッジ追従と任意位置の追っかけを切替できる。弾幕の表示切替・不透明度・速度・表示領域・サイズ・種別・削除済み切替はプレイヤー上にも反映する。ライブエッジ復帰で seekable range の末尾へ移動する。既存の chat offset を動画の再生時刻に合わせて弾幕表示し、追っかけ中はコメントを再取得する。利用者は次の案を採用しない:

- YouTube embed は保存動画や保存コメントと同期しない。
- yt-dlp の MPEG-TS progressive 出力は seek が弱く、追っかけに適さない。
- `yt-dlp -g` URL を ffmpeg が直接取得する方式は、長時間配信で期限切れになる manifest URL のローテーションを yt-dlp に任せられず、録画の信頼性を下げる。
# 文字起こしと処理キュー

録画完了後に `TRANSCRIBE` ジョブを `processing_jobs` へ冪等に登録する。APScheduler の `processing-queue` が pending / retry_wait を取得し、PostgreSQL advisory lock（`processing-job` 名前空間）で多重実行を抑止する。録画を10分区間で処理し、各区間の raw / 整形済み segment と cursor を同一トランザクションで確定する。中断時は最後に確定した区間から再開し、lock を再取得できることを確認して retry_wait へ戻す。失敗は指数バックオフ（60秒開始、最大24時間）で再試行し、`PROCESSING_MAX_ATTEMPTS` に達すると failed にする。

ジョブ状態と `videos.transcription_status` は独立しており、文字起こしが失敗しても `videos.status` の録画状態を変更しない。録画ディレクトリのメディアを読み取り専用で ffmpeg に渡し、一時領域へ mono / 16 kHz / PCM WAV を作成する。faster-whisper は遅延 import するため、モデル取得・推論はジョブの初回実行時だけ発生する。CPU の compute type が auto の場合は int8 を選ぶ。WhisperX、話者分離、LLM/OpenRouter 要約は含まない。

正データは `transcript_segments` の raw / 整形済み行と `transcript.json`。画面内で時刻からプレイヤー位置へ seek でき、本文検索と100件単位のページングに対応する。JSON / WebVTT / SRT / text を API から取得できる。整形はタイムスタンプを維持し、空行・空白・連続完全重複だけを保守的に処理する。Retention の既存 Fail Safe 判定は変更していない。削除が許可された録画ディレクトリ全体を削除するため、その中の派生ファイルも録画と同時に追従し、保護動画は従来通り削除対象から除かれる。

## LLM 階層要約（Phase 2）

要約は OpenRouter を介した任意機能で、既定は無効です。`OPENROUTER_API_KEY` と設定可能な `OPENROUTER_MODEL` があり、GUI または環境変数で有効化した場合にだけ独立した `SUMMARIZE_CHUNKS` / `SUMMARIZE_FINAL` ジョブを作ります。送信するのはローカル文字起こしのタイムスタンプ付きテキストだけです。動画・音声を送らず、録画・文字起こし・字幕・検索の成功条件には含めません。

`SUMMARY_CHUNK_MINUTES` は 15 / 20 / 30 / 60 分から選べます。発話単位で分け、区間要約を作成した後、区間要約のみを使って全体要約を作ります。JSON をパース・スキーマ検証し、キャッシュキー `SHA256(transcript + prompt_version + model)` で再送を避けます。失敗は要約ジョブだけに `retry_wait` / `failed` を記録し、429 の Retry-After と指数バックオフを適用します。最大並列数・試行数は保守的な設定です。

動画詳細から全要約、全体要約のみ、別モデルでの再生成が可能です。設定画面では API キーをマスクし、外部プロバイダーへ文字起こしが送られ得ることを表示します。API キー・文字起こし全文は通常ログへ出力しません。


## 全文検索とタイムライン（Phase 3）

`GET /api/search/transcripts` は録画済み動画の正規化文字起こしをページング検索し、チャンネル・録画期間を併用できる。PostgreSQL の `pg_trgm` が導入済みなら `%` trigram 演算子も検索候補に使い、拡張がない場合・権限不足時は `ILIKE` 部分一致へ自動フォールバックする。日本語の形態素解析に依存せず、短い語も部分一致で拾うため `ILIKE` を常に併用する。拡張導入を必須にしない。結果には発言時刻、動画タイトル、前後抜粋と独立した一致範囲を含める。

`GET /api/videos/{id}/timeline` は要約 `topics`、次に `timeline` を採用し、どちらも空なら正規文字起こしセグメントを時刻順に返す。データが無い場合は空配列。GUI からの検索・タイムライン選択は対象録画を開いてプレイヤーの `currentTime` を指定する。

## ワーカー構成と処理パイプライン

APScheduler の単一 backend 常駐プロセスがチャンネル監視、ライブ検出、録画投入、復旧、Retention / Protection を周期実行する。録画子プロセスは video 単位の advisory lock 下で動作する。録画完了後にメディア検査・duration / fingerprint を記録し `ACTIVE` へ遷移した後、独立した DB `processing_jobs` キューが faster-whisper 文字起こしを実行し、任意設定時のみ OpenRouter 要約を別ジョブで処理する。再試行状態・セグメント・要約は録画状態と分離する。追っかけ再生は tee で通常保存と上限制御付き HLS を並行生成する。

状態遷移と削除条件は要件30章に従い、Remote 確認不能、出力欠落、失敗は削除根拠にならない。PostgreSQL 接続には `idle_in_transaction_session_timeout=60000` を設定し、依存関数終了時に未完了トランザクションを rollback して session を close する。

## 要件30章: 削除原則と実装上の約束

自動削除は PUBLIC、UNCHANGED、Remote 確認成功、期限超過、Grace 超過のすべてを満たし、過去の成功確認も存在するときだけ許可する。失敗・NULL・未対応状態は KEEP / `PROTECTION_PENDING` に倒す。削除直前に video lock の保持を再確認し、ファイル削除成功後にだけ DB 状態を `DELETED_BY_RETENTION` へ変更する。元 video 行と監査ログを残す。保護対象は Retention Worker から除外する。録画失敗や `.part` / `.ytdl` の存在だけで最終成果物を削除してはならず、完全な成果物が存在しサイズ>0と確認できた場合に限って対応する中間ファイルを整理する。
