# YouTube Live Archiver

複数の YouTube チャンネルを監視し、ライブ配信とアーカイブを保存するセルフホスト用アプリです。Backend は FastAPI / PostgreSQL、画面は Vue 3 / TypeScript で構成します。YouTube 取得に `yt-dlp`、メディア処理に `ffmpeg` と `ffprobe` を使います。

## セットアップと起動

ローカル実行は Python 3.11 以上、Node.js / npm、`ffmpeg` が必要です。PostgreSQL と `yt-dlp` は Python 依存関係に含まれ、初回起動時に PostgreSQL を `backend/data/postgres` で自動起動します。録画ファイルの既定保存先は `backend/data/archive` です。

### macOS / Linux（ローカル実行）

`ffmpeg` の導入例です。

```sh
# macOS
brew install ffmpeg
# Ubuntu / Debian
sudo apt update && sudo apt install ffmpeg
```

リポジトリのルートで次を実行します。.env の作成は任意です。

```sh
cd backend
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt -r requirements-dev.txt
cd ../frontend
npm ci
npm run build
cd ../backend
python -m app
```

### Windows（ローカル実行）

Python 3.11 以上と Node.js をインストールし、PowerShell で `winget install -e --id Gyan.FFmpeg` を実行します。新しい PowerShell を開き、リポジトリのルートで次を実行します。

```powershell
Set-Location backend
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt -r requirements-dev.txt
Set-Location ..\frontend
npm ci
npm run build
Set-Location ..\backend
python -m app
```

起動ログに組み込み PostgreSQL の場所と `http://0.0.0.0:8000/ で待受中` が表示されます。ブラウザーでは `http://localhost:8000/` を開きます。ホーム画面で保存先を登録後、チャンネルを登録すると監視と録画を開始できます。ローカル実行では PostgreSQL と yt-dlp の別途インストールは不要です。

起動ログ例:

```text
組み込み PostgreSQL: /.../backend/data/postgres
アプリデータ: /.../backend/data
保存先ルート: /.../backend/data/archive
http://0.0.0.0:8000/ で待受中
```

外部 DB に接続できない場合は次のように表示して、HTTP server を起動せず終了します。

```text
データベースに接続できません: connection refused。backend/.env の DATABASE_URL（現在: localhost:5432/yla）を確認してください。
```

### ローカル実行の設定

`.env.example` を `backend/.env` にコピーするのは任意です。既定値のまま起動できます。外部 PostgreSQL、待受ポート、保存先を変更するときだけ `backend/.env` を作成し、例のように設定します。相対パスは backend ディレクトリ基準で絶対パスに解決されます。

```dotenv
DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/yla
APP_PORT=8010
STORAGE_ROOT=./recordings
```

外部 PostgreSQL は起動時に接続確認されます。接続できない場合は最大 `DATABASE_CONNECT_RETRY_SECONDS`（既定30秒）再試行後に終了します。`AUTO_MIGRATE=false` の場合は schema が古いと起動せず、`python -m alembic upgrade head` を案内します。

### データの保管と移行

組み込み PostgreSQL は `backend/data/postgres` に保存されます。停止後にアプリ全体のバックアップを取る場合は、録画データの `backend/data/archive` と合わせて `backend/data` をコピーします。稼働中の PostgreSQL データディレクトリをコピーしないでください。外部 PostgreSQL へ移行する場合は、アプリ実行中に dump を取り、その後アプリを停止して外部 DB に restore します。

```sh
# アプリ実行中、backend ディレクトリで実行。Postgres の一貫した snapshot を dump する。
# Linux / macOS: postmaster.pid の5行目が socket directory。
PG_DATA=./data/postgres
PG_SOCKET="$(sed -n '5p' "$PG_DATA/postmaster.pid")"
pg_dump -h "$PG_SOCKET" -U postgres -d postgres -Fc -f yla.dump
createdb -h localhost -U user yla
pg_restore -h localhost -U user -d yla --no-owner yla.dump
# 接続確認例
psql 'postgresql://user:pass@localhost:5432/yla' -c 'SELECT version_num FROM alembic_version'
```

Windows では `backend\data\postgres\postmaster.pid` の4行目が port です。アプリ実行中に `pg_dump -h 127.0.0.1 -p <port> -U postgres -d postgres -Fc -f yla.dump` を実行してからアプリを停止します。移行後、`backend/.env` の `DATABASE_URL` を外部 URL に変更して起動します。利用環境に応じて `pg_dump` / `pg_restore` を組み込みサーバーのバージョンと合わせてください。

### Docker Compose

リポジトリ直下で `docker compose up --build` を実行します。Compose は `docker-compose.yml` で `DATABASE_URL` を `db` サービス名に設定し、永続 volume に PostgreSQL を保存します。ルート `.env` の `POSTGRES_PASSWORD` と `ARCHIVE_PATH` は Compose 専用です。`.env.example` のローカル `DATABASE_URL` は空欄のままにしてください。Compose backend も起動時に自動 migration します。

### Windows サービス

サービスの `.env` に既存 PostgreSQL の `DATABASE_URL` を明示します。組み込み DB へ切り替える設定変更は不要です。サービス起動時に DB 接続確認と自動 migration が動くため、サービスアカウントに DB 接続権限と `DATA_DIR` / `STORAGE_ROOT` の書き込み権限を設定します。更新前バックアップ、サービス停止、更新、起動後 `/api/status` とログの確認を行います。自動 migration を止める場合は `AUTO_MIGRATE=false` とし、別途 `python -m alembic upgrade head` を実行します。

このアプリには認証がなく、CORS は任意の origin を許可します。信頼できる LAN / tailnet 内で使い、インターネットへ直接公開しないでください。

### 初回設定

1. 「ストレージ」で書き込み可能な保存先を登録し、必要なら既定の保存先にします。
2. 「チャンネル」で YouTube URL、`@handle`、チャンネル ID のいずれかを登録します。登録時に名前とアイコンを取得し、初回スキャンを実行します。
3. チャンネル設定で監視、保存先、保存期間、画質、Cookie Profile、チャット、追っかけ視聴などを調整します。

登録後のメタデータ更新はチャンネルの「情報更新」、今すぐの再スキャンは「今すぐ確認」を使います。

## 画面と主な操作

| 画面 | 主な操作 |
| --- | --- |
| ホーム | 監視・録画の概要、配信予定、最近の録画、システム状態を確認します。 |
| チャンネル | チャンネルを登録・編集・削除し、監視、次回確認時刻、予定数、録画数、失敗状態を確認します。削除は論理削除です。 |
| 配信予定 | 予定配信を検索・絞り込みし、録画予約の有効 / 無効を切り替えます。 |
| アーカイブ | 録画一覧を検索・絞り込み・並べ替えし、再生、ダウンロード、保護、削除、変換、録画再試行、停止を操作します。 |
| 全文検索 | 文字起こし本文を検索し、チャンネルや録画期間で絞り込みます。結果から録画の該当位置へ移動できます。 |
| ストレージ | 保存先の追加・編集・削除、既定先、使用量・空き容量を確認します。 |
| 分析 | チャンネル統計と録画量の推移を確認します。統計は取得開始後のスナップショットです。 |
| アクティビティ | 監視・録画・保護・Retention などの操作履歴と状態を確認します。 |
| 設定 | 表示、録画の実効設定、画質、録画ルール、文字起こし、要約を設定します。 |

アーカイブの削除は確認ダイアログを経て監査ログに記録されます。手動保護を解除しない限り自動 Retention の対象にはなりません。

## 設定

`.env.example` には backend runtime が参照する環境変数、既定値、説明を記載しています。UI から変更できる項目は DB のアプリ設定が環境変数の値を上書きする場合があります。保存先やチャンネル固有の項目は画面から設定します。

主な既定値は次のとおりです。

- `DATABASE_URL`: PostgreSQL 接続 URL
- `STORAGE_ROOT`: 基本保存ルート（コンテナ内は `/archive`）
- `DOWNLOADER=native`: yt-dlp の標準 downloader。必要時に `aria2c` を選べます。
- `LIVE_QUALITY` は空で形式を固定せず、`ARCHIVE_QUALITY` は AV1、VP9、best の順に選びます。
- `MAX_CONCURRENT_LIVE_RECORDINGS=8`、`MAX_CONCURRENT_ARCHIVE_DOWNLOADS=2`
- `LIVE_FROM_START=true`、`LIVE_FROM_START_MAX_AGE_HOURS=12`
- `TRANSCRIPTION_ENABLED=true`、`WHISPER_MODEL=large-v3-turbo`。モデルの初回利用時にダウンロードされます。
- `SUMMARY_ENABLED=false`。有効化には OpenRouter の API キーとモデルが必要です。

環境変数の詳細な一覧は [.env.example](.env.example) を参照してください。チャンネルの画質、保存先、保存期間などの優先値は画面設定に従います。録画ルールはタイトル / 説明文のキーワードまたは正規表現で録画対象を選別します。

## 監視

チャンネル登録直後に初回スキャンし、通常スキャンは既定で `AUTO`（30〜60 分の範囲）です。チャンネルごとに 30 / 45 / 60 分または AUTO を指定できます。予定配信の開始時刻を動画情報から取得し、開始が近づくと通常間隔を上限として確認間隔を短縮します。予定情報がない場合も候補を定期確認します。

開始予定の30分より前（最大24時間前）は5〜10分、30分以内は1〜3分、5分以内は15〜30秒へ短縮します。数値指定の通常間隔は短縮間隔の上限として働きます。一時的なスキャン失敗は連続失敗数に応じて指数バックオフし、成功後にリセットします。アクセス制限を検出すると全チャンネルのスキャンをクールダウンし、連続検出時は10、20、40、最大60分へ延びます。

ホームとチャンネル画面では、監視中・確認待ち・停止中・録画中・エラーなどの健全性、直近の成功 / 失敗、次回確認時刻を表示します。システム状態と scheduler job の実行状況は `/api/status`、イベントは `/api/events` から確認できます。

## 録画と復旧

- ライブ録画と終了済みアーカイブ取得は別々の同時実行枠で処理します。既定はライブ8件、アーカイブ2件です。
- ライブチャットは映像録画とは別の yt-dlp プロセスで並行取得します。チャット取得失敗は映像録画の成功を取り消しません。
- 通常のライブ録画は検出時点から開始します。配信先頭からの録画はチャンネルと全体の設定が有効な場合に `yt-dlp --live-from-start` で試みます。配信開始時刻を取得できた配信は開始から12時間を超えると対象外にし、時刻不明なら先頭取得を試します。追っかけ視聴と併用するチャンネルでは先頭取得を使わず通常録画にします。先頭取得の yt-dlp がエラー終了し media が残らない場合は `--live-from-start` を外して通常録画へ再試行します。
- 追っかけ視聴を有効にすると ffmpeg tee で保存用メディアと一時 HLS を並行生成します。HLS は設定したサイズ上限まで保持し、録画中に再生できます。tee の起動・実行に失敗した場合は HLS を破棄して通常録画を再起動します。
- 録画停止はその backend プロセスが所有する子プロセスを停止します。再生可能な出力が救出できたユーザー停止録画は部分録画として保持し、自動再開しません。
- アプリ終了やプロセス異常終了で出力が途切れた場合は部分ファイルを保全し、起動時・定期復旧で YouTube 状態を確認します。公開中なら別 stem / 区間で録画を再開し、終了済みならアーカイブ取得を試みます。非公開・削除・視聴不能は保護し、判定不能はバックオフして再確認します。試行上限に達したものは `recovery_exhausted` として自動再試行を止めます。
- 冒頭欠け、復旧による複数区間、配信先頭取得の不足はアーカイブ補完をキューに入れる場合があります。補完アーカイブを duration tolerance と照合し、完全と判断した場合に primary media を切り替え、部分録画フラグを解除します。
- 完了時は出力の存在・サイズ・duration などを確認し、録画情報・checksum・fingerprint を更新します。integrity worker は ACTIVE 録画のメディア欠落・空ファイルを確認し、見つけると監査記録を作って `RECORDING_FAILED` に戻し、復旧確認へ渡します。
- 録画中にストレージの空き容量が警告しきい値を下回ると警告イベントを記録します。録画開始時に保存先へ書き込み可能か、最低空き容量を満たすか確認します。

Retention 対象になるのは `ACTIVE` かつ完全な録画です。部分録画は保持期限や自動削除の対象外です。録画失敗・停止・後処理失敗だけを理由にファイルを削除しません。

## Retention と整合性保護

Retention は保存期限後に YouTube の状態と編集有無を確認し、削除猶予の後にもう一度確認します。2回目の削除直前確認でも公開状態かつ未編集であり、独立した成功確認がそろった場合にだけ削除します。

編集検出は OFF / Basic / Standard / Strong です。Basic は duration、Standard は duration と映像 fingerprint、Strong は映像と音声 fingerprint を比較します。Standard / Strong の録画 fingerprint は元動画の5 / 20 / 40 / 60 / 80 / 95%地点から採取します。許容 duration 差は `DURATION_TOLERANCE_SECONDS`、fingerprint しきい値は `FINGERPRINT_VIDEO_THRESHOLD` / `FINGERPRINT_AUDIO_THRESHOLD` で指定します。遠隔サンプル、duration、fingerprint の取得に失敗して判定できない場合は `PROTECTION_PENDING` に保留し、自動削除を許可しません。手動保護、非公開、削除、視聴不能、編集検出は保護状態になります。

部分録画は Retention の候補抽出と削除判定の両方で除外します。中間ファイルは検証済みの同 stem の完成ファイルがある場合に限り整理します。

## チャットとプレイヤー

チャットは通常コメント、スーパーチャット / スーパーステッカー、メンバーシップ、ギフト関連を保存します。絵文字画像は `DATA_DIR/chat-assets` にローカルキャッシュし、画面からはアプリ内のキャッシュ URL で読み込みます。コメントの再生時刻はライブ開始からの offset を録画開始 offset 分補正します。途中から録画した場合、録画より前のコメントは表示時刻をゼロへまとめず除外します。

アーカイブの「チャット再取得」は replay を取得してから取り込みます。取得不能時は既存コメントを維持し、バックグラウンドで再試行を予約する場合があります。「再解析」は保存済み JSONL を使ってコメントを再解析します。動画の再生位置に合わせてチャット一覧を追従します。

DPlayer の標準ホットキーを有効にしています。歯車メニューには再生速度、ループ、弾幕表示、不透明度、流れる速さ、文字サイズ、表示領域、弾幕の量、コメント種別（すべて / スーパーチャット / メンバーシップ）、削除済みコメントの表示設定があります。ライブ録画中はライブ追従と任意位置の追っかけを切り替えられます。追従設定時はライブ端から遅れた場合に追従位置へ戻ります。`d` キーで弾幕表示を切り替え、`Escape` キーで設定パネルを閉じるかプレイヤーを閉じます。設定の拡張は `frontend/src/lib/dplayerSettings.ts` で DPlayer の設定パネルへ項目を追加します。

## 文字起こし、要約、検索

録画完了後、任意設定で faster-whisper による文字起こしを処理キューへ登録します。失敗・再試行は録画状態から独立しています。文字起こしは動画詳細、全文検索、字幕出力から利用できます。要約は既定で無効で、OpenRouter を使う場合はタイムスタンプ付き文字起こしテキストを外部 API へ送ります。動画・音声ファイルは送信しません。

## トラブルシューティング

| 症状 | 確認箇所 |
| --- | --- |
| `frontend/dist` が無い | `cd frontend && npm ci && npm run build` を実行します。起動は続きますが `/` は案内 HTML の 503 を返します。 |
| DB 接続エラー | 起動ログに表示された `host:port/database` と `backend/.env` の `DATABASE_URL` を確認します。外部 DB は既定30秒再試行します。組み込み DB の導入に失敗した場合は `python -m pip install pgserver` を実行します。 |
| ポート使用中（`Address already in use`） | `APP_PORT` を空いている番号へ変更して再起動します。 |
| ffmpeg が無い | macOS は `brew install ffmpeg`、Ubuntu / Debian は `sudo apt install ffmpeg`、Windows は `winget install -e --id Gyan.FFmpeg` を実行します。 |
| `/api/*` が DB 接続エラー | backend 上部に DB 切断を表示します。DB が復旧すると API 成功時に表示が消えます。 |
| チャンネル情報を取得できない | `yt-dlp --version`、ネットワーク、Cookie Profile、チャンネルのアクセス権を確認します。 |
| 配信が検出されない | チャンネル監視状態、`next_check_at`、最終成功 / エラー、システム `/api/status` を確認します。アクセス制限時はクールダウン終了を待ちます。 |
| 録画が始まらない | 録画枠、保存先の有効 / 書込権限 / 空き容量、`WAITING_FOR_STORAGE`、動画状態、`yt-dlp` ログを確認します。 |
| 録画が部分状態のまま | `recording.json`、録画ディレクトリの stem ごとのファイル、復旧履歴、YouTube 上の公開状態を確認します。保全ファイルを手動で消さないでください。 |
| 再生できない | ブラウザ対応形式を確認します。MKV が再生できない場合はダウンロードまたは MP4 へのリマックス / H.264 変換を使います。 |
| 文字起こしされない | 設定の有効状態、処理ジョブ、ffmpeg、Whisper のモデル / device / compute type、ログを確認します。 |
| Retention が保留される | Remote 状態、`remote_checks`、編集検出モード、ffmpeg、fingerprint と duration の取得状況を確認します。保留中のファイルは削除しません。 |

## テストと画面キャプチャ

`YLA_TEST_DATABASE_URL` を省略すると、backend pytest と Playwright E2E は pgserver を一時ディレクトリで起動し、終了時に片付けます。外部 PostgreSQL を使う場合は、`postgresql+psycopg://...` の専用テスト server（DB 作成権限あり）を指定してください。

README の初回起動手順を一時コピーと新しい venv で確認する smoke test です。依存導入・frontend build を含み、実 YouTube へアクセスしません。

```sh
python scripts/smoke_first_run.py
```

```sh
cd backend
python -m pytest -q -p no:cacheprovider
```

Frontend の各確認コマンドです。

```sh
cd frontend
npm run format:check
npm run lint
npm run build
npm run test:unit
npm run test:e2e
npm run shots
```

E2E と shots は一時 PostgreSQL・backend・frontend を起動します。詳細と外部サービスへ接続する実配信手順は [docs/E2E_TESTING.md](docs/E2E_TESTING.md) を参照してください。

## 未実装

- 要件の保存パス Template 設定。現行保存先は `{storage}/{channel_id}/{year}/{video_id}` 固定です。
- 通知機能。
- 復元可能なゴミ箱。手動削除後のファイル復元機能はありません。
- 簡易認証。API と画面にログイン認証はありません。
- 要件で想定する数十〜数百チャンネルの長期運用の実証。

## 機能と検証状況（2026-10-04）

この節の「実機で確認済み」は、本書冒頭に記載された同日 macOS / yt-dlp 2026.8.19 / 実 YouTube の実機確認記録だけに基づきます。自動テストの結果は実機での確認とは分けて記載します。

### 実機で確認済み

- URL / ID からのチャンネル登録、名前・アイコン取得、登録直後スキャン、ライブ・予定・過去配信の検出。
- 予定開始時刻の取得と直前の短縮ポーリング。07:00 予定の配信を開始33秒後に録画開始。
- ライブ2本とアーカイブ1本の同時処理、チャット別プロセス取得、録画中の映像ファイル増加。
- 自然終了後の完了処理、冒頭欠けの検出、自動アーカイブ補完、35,757秒の全編取得、primary 切替と部分録画フラグ解除。
- ユーザー停止後の再生可能ファイル保持と自動再開抑止、アプリ再起動後の別区間での録画再開。
- 過去配信64分の取り込み、チャット replay 6,057件の取り込み、再解析、絵文字キャッシュ表示、再生位置追従。
- Retention 二段階確認から削除まで（初回確認35秒、猶予後の最終確認28秒、ディレクトリ削除と監査ログ）。
- 配信先頭からの録画、`.part` 増加、ユーザー停止時の570秒分の再生可能ファイル、開始から12時間超の対象外判定。

### 自動テスト

Round 9 の最終ローカル実行結果は次のとおりです。

- backend pytest: 304 passed（指定 PostgreSQL URL と `YLA_TEST_DATABASE_URL` 無指定の両方）
- frontend format:check: 成功
- frontend lint: 成功
- frontend build: 成功
- frontend test:unit: 41 passed
- frontend E2E: 45 passed（PC / tablet / mobile）
- 初回起動 smoke: 新規 venv で成功。README 手順、組み込み DB、migration、画面、yt-dlp、保存先登録を確認
- frontend shots: Round 8 で review 74枚を生成し、歯車メニューを目視確認

### 未検証（理由）

- Windows 実行: Windows 環境を今回使っていないため。CTRL_BREAK による停止、部分録画救出、サービス運用を含みます。
- 先頭から録画した配信の自然終了までの一連動作: 実機確認はユーザー停止までの範囲です。
- 追っかけ視聴の ffmpeg tee 実配信: 実配信での動作を確認していません。
- faster-whisper の実推論: 実モデルで確認していません。
- OpenRouter 実 API: 外部 API 呼び出しを確認していません。
- 非公開・削除・編集動画に対する Retention 保護: 実 YouTube での保護動作は未検証です。単体・統合テストのみです。
- 数十〜数百チャンネルの長期運用: 規模・期間を満たす稼働を確認していません。
- スーパーチャットを含む実チャット表示: 通常チャット等の確認と分けて、実スーパーチャットを確認していません。
