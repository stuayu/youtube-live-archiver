# YouTube Live Archiver

複数の YouTube チャンネルを監視し、ライブ配信を `yt-dlp` で保存するセルフホスト用アプリです。FastAPI / PostgreSQL / Vue 3 で構成します。

## Windows ローカルセットアップ（Docker 不要）

1. PostgreSQL 17 をインストールして起動し、アプリ用 DB とユーザーを用意します。例: `yla` DB、`postgres` ユーザー。
2. リポジトリ直下と `backend/` の `.env` を作成し、`DATABASE_URL`、`STORAGE_ROOT` と下記設定を調整します。
3. `cd backend; python -m pip install -r requirements.txt` を実行します。`yt-dlp` と `ffmpeg` は PATH に必要です。
4. frontend をビルドします: `cd frontend; npm install; npm run build`。生成された `frontend/dist` は FastAPI が `/` で配信します。
5. `cd backend; alembic upgrade head` で DB を更新します。
6. `cd backend; python -m app` で起動します。GUI は <http://localhost:8000/>、API は <http://localhost:8000/docs> です。
7. ストレージを登録してからチャンネルを登録します。登録時は正規 `UC...` ID を指定できます。

設定例は `.env.example` を参照してください。`APP_HOST` の既定値は `0.0.0.0`、`APP_PORT` は `8000` です。CORS は任意の origin を許可する設定で、アプリに認証機構はありません。信頼できる LAN または tailnet 内だけで利用し、ネットワーク境界を信頼境界として扱ってください。インターネットへ直接公開しないでください。

録画完了後の文字起こしは Python 製の DB ジョブキューからローカル `faster-whisper` で処理します。`TRANSCRIPTION_ENABLED`、`WHISPER_MODEL`、`WHISPER_DEVICE`、`WHISPER_COMPUTE_TYPE`、`WHISPER_LANGUAGE`、`TRANSCRIPTION_MAX_CONCURRENCY` は環境変数、または GUI の「設定」から変更できます。初回実行時は faster-whisper が選択モデルをローカルに取得します。ジョブ・再試行・セグメントは DB に保存し、録画本体の状態とは独立しています。詳細は [文字起こしと処理キュー](docs/ARCHITECTURE.md#文字起こしと処理キュー) を参照してください。

LLM 要約（Phase 2）は既定で無効です。利用時は設定画面または `.env` の `OPENROUTER_API_KEY`、`OPENROUTER_MODEL` を設定し、要約を明示的に有効化してください。`OPENROUTER_BASE_URL`、`SUMMARY_CHUNK_MINUTES`、`OPENROUTER_MAX_CONCURRENCY`、`OPENROUTER_MAX_ATTEMPTS` も変更できます。OpenRouter へ送信するのはローカルで生成済みのタイムスタンプ付き文字起こしテキストだけで、録画動画・音声は送信しません。要約を無効にしても録画・文字起こし・字幕・検索は利用できます。外部プロバイダーへ文字起こし内容を送信し得るため、送信内容を確認したうえで有効化してください。詳細は [階層要約](docs/ARCHITECTURE.md#llm-階層要約-phase-2) を参照してください。

録画中の追っかけ視聴はチャンネル詳細設定で個別に有効化します（既定 OFF）。有効時は ffmpeg tee が保存用 MKV と HLS を同時に生成し、上限 bytes を指定できます。tee が失敗した場合は通常の yt-dlp 録画へ切り替えます。詳しくは [アーキテクチャ](docs/ARCHITECTURE.md#録画中の追っかけ再生) を参照してください。

録画失敗・録画プロセスの異常終了・heartbeat stale・出力メディア欠落は、保存ファイルを削除する理由になりません。部分ファイル、info.json、サムネイルを保全し、エラーログを録画ディレクトリの親へ保存します。削除は [Retention の安全原則](docs/ARCHITECTURE.md#failure-artifacts-and-deletion-safety) に従い、要件30章の条件確認後に限ります。

チャンネル設定の「過去配信の取込数」は登録済みの過去アーカイブを何本録画するかを指定します。既定値 `0` は過去分を録画せず、監視開始後のライブのみを対象にします。過去分は一覧に `SKIPPED` として表示されます。チャンネル登録後に名前・アイコンなどを再取得するには「情報更新」を使います。アイコンは `DATA_DIR`（既定 `./data`）配下へキャッシュします。

### 画質・フォーマット設定

MKV 録画は動画一覧から MP4 リマックス（再エンコードなし）を要求できます。AV1 非対応端末向けの H.264 + AAC 変換も明示操作でキューに追加できます。変換結果は別ファイルになり、元録画のダウンロードも引き続き可能です。

ライブ録画と終了済みアーカイブでは画質設定を分けています。ライブは既定で「オリジナル（自動選択）」となり、yt-dlp に `-f` を渡しません。ライブ配信で形式指定が原因の失敗を避け、取りこぼしを抑える設定です。終了済みアーカイブは既定で AV1 → VP9 → 利用可能な最良形式の順に選びます。グローバル既定値は backend の `LIVE_QUALITY` / `ARCHIVE_QUALITY` 環境変数でも設定できます。

プリセットは「オリジナル」「AV1 優先 高画質」「VP9 優先 高画質」「H.264 優先」「1080p まで」「720p まで」「音声のみ」です。必要な場合はカスタム欄へ yt-dlp format selector を指定できます。設定の優先順位は動画個別、ルール、チャンネル、グローバル既定値です（ルール録画と動画個別設定は現在未実装）。変更は次回録画から適用し、録画中の selector は変えません。

指定形式が利用できず yt-dlp が失敗した場合は、自動選択（`-f` なし）で再試行します。適用した selector と fallback 状態は録画レコードと `recording.json` に記録されます。再試行も失敗すると `RECORDING_FAILED` とし、`<video_id>-recording-error.log` にエラーを保存します。フォーマット確認欄はボタン操作時だけ YouTube へ問い合わせます。

AV1 はエンコード・デコードが重く、古い機器やブラウザでは再生できない場合があります。再生互換性を重視する場合は H.264 を選んでください。MKV はブラウザ再生に対応しないことがあるため、必要に応じてファイルをダウンロードして再生してください。

「分析」タブは、チャンネルメタデータの取得に成功した時点の統計スナップショットを表示します。記録間隔は `CHANNEL_STATS_INTERVAL_HOURS`（既定24時間）です。設定後から統計を蓄積し、存在しない過去データは補完しません。登録者数は yt-dlp が取得した整数を保持し、YouTube の表示値が丸められている可能性も記録します。

アーカイブ画面は30件ずつページングします。録画ファイルの Range 配信は動画 ID に紐づく DB パスを登録 Storage の配下で検証してから行います。ブラウザ再生に対応しない MKV はダウンロードして再生してください。手動削除は確認ダイアログ後のみ実行され、操作履歴に記録されます。

### aria2 ダウンロード

`DOWNLOADER=aria2c` が既定です。`aria2c` が PATH に無い場合は自動的に yt-dlp 標準 downloader にフォールバックします。必要なら `ARIA2C_PATH`、`ARIA2C_CONNECTIONS`（-x）、`ARIA2C_SPLIT`（-s）、`ARIA2C_MIN_SPLIT_SIZE`（-k）、`ARIA2C_MAX_CONCURRENT_DOWNLOADS`（-j）を設定します。Windows では `winget install aria2.aria2` で導入できます。

aria2c は終了済みアーカイブ、Remote fingerprint 用の section、その他の静的ファイル取得に使います。ライブ録画中の HLS はリアルタイムで流れるため、分割並列取得の対象に向かず、常に yt-dlp の native downloader を使います。

### Windows サービス

管理者 PowerShell からリポジトリ直下で実行します。NSSM が未導入なら `winget install nssm` を実行後にスクリプトを再実行します。NSSM が使えない場合は起動時タスクを利用できます。

```powershell
.\scripts\install-windows-service.ps1
# または
.\scripts\install-windows-service.ps1 -UseScheduledTask
```

サービスは PostgreSQL `postgresql-17` に依存し、異常終了時に再起動します。ログは `logs/service-stdout.log` と `logs/service-stderr.log` に出力されます。解除は ` .\scripts\uninstall-windows-service.ps1` です。

### LAN / Tailscale とファイアウォール

Private プロファイルだけで TCP `APP_PORT` を許可するには、管理者 PowerShell で ` .\scripts\setup-firewall.ps1` を実行します。Tailscale の送信元範囲も許可する場合は ` .\scripts\setup-firewall.ps1 -AllowTailscale` を使います。

同じ宅内 LAN からはこの PC の LAN IP を使って `http://<LAN-IP>:8000/` にアクセスします。Tailscale では `http://100.115.154.148:8000/`（`stuayu-miyagi`）です。認証がないため、ルーターでポート転送したり、インターネットへ直接公開したりしないでください。

## チャンネル設定

一覧から監視 ON/OFF、間隔（30/45/60/AUTO）、保存先と Fallback Storage、保存期間、削除猶予、画質、Cookie Profile、自動保護、編集検出モードを編集できます。チャンネル単位の「常に永久保存」と、録画中の停止・失敗録画の再試行にも対応しています。チャンネル削除は論理削除し、動画履歴を維持します。

Cookie Profile は Netscape 形式 Cookie ファイルのパス、または `chrome[:profile]` 等を指定します。Docker 利用時は Cookie ファイルを backend コンテナへ読み取り可能な形でマウントしてください。ホストのブラウザ profile はコンテナから見えないため、コンテナ内で利用できるブラウザ profile のみ指定できます。

## 監視と録画

通常監視はチャンネル設定に従い、AUTO は30〜60分の jitter を加えます。Upcoming 配信は予定時刻に応じて 5〜10分、1〜3分、15〜30秒間隔へ短縮し、開始検出後に yt-dlp へ引き渡します。チャンネルごとに次回時刻をずらします。

録画開始時に保存先の書き込みと空き容量を確認し、Primary が利用できなければ設定済み Fallback、続いて Default Storage を試します。保存先が利用できなければ `WAITING_FOR_STORAGE` で待機します。異常終了・GUI停止時は部分録画を保持して `RECORDING_FAILED` にします。起動時と毎分の復旧ジョブが YouTube 状態を確認し、配信中なら別パートで再開、公開済みならアーカイブを取得します。非公開・削除済み・視聴不能なら部分録画を保護し、判定不能時は指数バックオフで保留します。

## 保持と削除の安全性

Retention は保持期限後に YouTube 状態と編集を確認し、設定された猶予期間後、削除直前にも再確認します。PRIVATE / DELETED / 利用不能 / 編集検出は保護し、通信・メタデータ・指紋の判定不能は `PROTECTION_PENDING` に留めます。初回と最終確認の両方で PUBLIC かつ未変更を確認できた場合だけ削除します。保護動画は Retention から除外し、ファイル削除後も履歴を残します。

編集検出モード:

- **OFF**: 編集検出を行いません。
- **Basic**: duration を比較します。
- **Standard**: duration と6地点（5/20/40/60/80/95%）の映像 perceptual hash を比較します。
- **Strong**: Standard に加えて音声 fingerprint を比較します。

比較しきい値は `FINGERPRINT_VIDEO_THRESHOLD`（既定0.25）と `FINGERPRINT_AUDIO_THRESHOLD`（既定0.35）で指定できます。動画/音声サンプル作成には `ffmpeg` が必要です。指紋の不足や取得失敗は自動削除許可にしません。

## 開発と確認

```powershell
cd backend
alembic upgrade head
python -m app
```

別ターミナルで `cd frontend; npm install; npm run dev` を実行します。

Retention / 排他の pytest は `cd backend; python -m pytest` で実行します（`backend/requirements.txt` の依存が必要です）。依存導入前はリポジトリルートから `python scripts/smoke_policy.py` を実行すると標準ライブラリだけで安全ポリシーの基本動作を確認できます。実配信での段階的な検証は [docs/E2E_TESTING.md](docs/E2E_TESTING.md) を参照してください。

## 現時点の制限

- Remote fingerprint 確認は yt-dlp の section download を使います。配信者側の形式や YouTube 側の制限によってサンプルが取れない場合は `PROTECTION_PENDING` となり、削除しません。
- 録画停止 API による子プロセス制御は、その Recorder を起動した backend プロセス内に限られます。
- 実録画と YouTube への Remote 確認にはネットワーク、`yt-dlp`、`ffmpeg` が必要です。

## YouTube ライブチャット

チャンネルの詳細設定で「ライブチャット保存」を切り替えます（既定 ON）。録画時に yt-dlp の live_chat 字幕を取得し、動画再生画面でコメント一覧・検索・種別絞り込み・削除済み表示・JSON export を利用できます。終了済みアーカイブの「チャット再取得」は動画の一覧データだけを更新し、チャット取得失敗で録画や既存コメントを変更しません。

録画途中からの配信は、配信開始時刻と録画開始時刻の差を差し引いて弾幕時刻をローカル動画へ合わせます。補正の詳細は [アーキテクチャ](docs/ARCHITECTURE.md#ライブチャット) を参照してください。


## ルール録画（自動予約）

設定タブでタイトル・説明文のキーワードまたは正規表現ルールを追加できます。対象チャンネル、有効/無効、大文字小文字、除外ルール、優先順位を設定でき、保存先・保存期間・画質・編集検出をルール単位で上書きできます。「既存動画でプレビュー」は保存済み videos と照合し件数を表示します。除外は包含より優先し、有効な包含ルールがある場合は不一致の候補を `SKIPPED` にします。包含ルールが無いチャンネルの従来の自動録画動作は維持します。

ルール録画の照合・保存先/保持設定の適用と、除外優先・正規表現・チャンネル範囲は pytest で確認済みです。説明文が既存動画の `raw_metadata` に無い場合、プレビューは空の説明文として照合します。

## PWA と API の応答

manifest、テーマ色、SVG アプリアイコンを用意し、スマートフォンからホーム画面へ追加できます。Service Worker によるオフライン再生はありません。API のエラー応答は `{"error":"..."}` 形式です。日時は UTC の ISO 8601 で返します（UTC オフセットは `+00:00` または `Z`）。GET のコレクション一覧は配列です。公開 API の ID は JSON 文字列で、内部 DB の主キーは整数のままです。ID を含む入力は URL / JSON 上で文字列として扱い、既存の数値キーを使う入力も受け付けます。

## 全文検索・タイムライン

「全文検索」タブから文字起こし済みの録画を横断検索できます。共通チャンネル選択で対象を絞り、録画詳細は発言の時刻をクリックするとその位置から再生します。検索はページングされます。PostgreSQL の `pg_trgm` が利用可能なら trigram を併用し、拡張未導入または利用権限が無い場合は `ILIKE` に切り替えます。拡張は任意です。動画詳細には要約の話題、なければ文字起こしセグメントを使うタイムラインが表示されます。

## 設定と運用

主な環境変数は `.env.example` にあります。`DATABASE_URL` は PostgreSQL 17 接続 URL、`APP_HOST` / `APP_PORT` は待受、`STORAGE_ROOT` / `DATA_DIR` は保存先、`POLL_INTERVAL_MINUTES` は監視、`RETENTION_*` と `DURATION_TOLERANCE_SECONDS` は保持判定、`TRANSCRIPTION_*` / `WHISPER_*` は文字起こし、`SUMMARY_*` / `OPENROUTER_*` は任意要約、`DOWNLOADER` / `ARIA2C_*` は取得方式、`CHANNEL_STATS_INTERVAL_HOURS` は統計周期です。実際に読み込む全キーと既定値は `.env.example` および `backend/app/config.py` を参照してください。

日常運用では GUI のストレージ空き容量と録画中モニタを確認し、バックアップ前に録画処理と PostgreSQL の状態を確認します。設定変更後の DB 更新は `cd backend; alembic upgrade head`。Windows サービス登録・解除は上記スクリプトを管理者 PowerShell で実行します。サービス稼働中の検証では既存ポートを使わず、検証用 backend の `APP_PORT` に空きポートを指定してください。

## トラブルシューティング

- `WAITING_FOR_STORAGE`: 保存先の接続・書込権限・最低空き容量を確認し、GUI から再試行します。
- `RECORDING_FAILED`: エラーログと部分成果物を保持したまま、監視状態と YouTube 公開状態を確認して再試行します。
- 文字起こしが `retry_wait`: ffmpeg、モデルの取得先、GPU/メモリを確認します。録画状態とは独立して再試行されます。
- 検索結果が空: 対象録画の文字起こし状態、キーワード、チャンネル絞り込みを確認します。pg_trgm は任意で、拡張なしでも部分一致検索できます。
- 自動削除保留: Remote 確認失敗・編集判定不足は安全側の動作です。ログと保護状態を調べ、未確認を理由に手動で一括削除しないでください。
- `npm run build` または画面撮影スクリプトが権限エラーとなる環境では、その制限を確認結果に記録してください。


## 機能と検証状況（2026-10-03）

### 実装済み

- チャンネル監視、ライブ/予定配信の検出、yt-dlp 録画、保存先の空き容量確認、復旧・Retention・Protection の各 worker。
- Remote が Public で未編集と確認できる場合に限る保持期限削除、保護、監査ログ。手動削除と Retention は別 API / 別状態で、手動削除の復元用ゴミ箱は未実装です。
- byte Range による録画ファイル配信、文字起こし/要約ジョブのコード、配信予定一覧、チャット取り込み処理、変換処理、分析、検索。
- ルール録画、自動除外、保存設定上書き、既存動画プレビュー。
- 公開 JSON の内部 ID を文字列で返す API 境界変換。DB の整数主キーは維持し、チャンネル/動画/保存先/ルール/チャット/検索/処理ジョブの応答を文字列 ID に統一。
- URL 状態を使うタブ/フィルター復元、1440/820/390 の responsive UI、manifest/icon による PWA インストール設定。
- subprocess 起動は引数配列を使用し、backend/app 内に `shell=True` / shell 起動 API はありません。`data/`、`archive/` は Git ignore 対象です。

### 実機・実環境で検証済み

2026-10-03 に次を実行しました。

- `backend`: `python -m pytest -q -p no:cacheprovider` — 99 passed。
- `backend`: `python ..\scripts\smoke_policy.py` — retention policy smoke checks passed。
- `backend`: `python -m alembic upgrade head` / `python -m alembic current` — `0014_recording_rules (head)`。
- `frontend`: `npm run build` — Vue/TypeScript と Vite build が成功。バンドルサイズ警告は残っています。
- `frontend`: `npm audit --omit=dev` — 0 vulnerabilities。DPlayer 経由の axios を修正版へ override しました。
- `APP_PORT=8011` 相当で試験専用 PostgreSQL DB を作成し backend を起動。`health`、`api/channels`、`api/videos`、`api/storages`、`api/storages/usage`、`api/quality-presets` はすべて HTTP 200。検証DBは終了後に削除しました。
- Playwright E2E は実ブラウザーで desktop 1440 / tablet 820 / mobile 390 を実行し、全7タブ、横幅、URL状態・リロード、チャンネル URL入力、主要操作の44px領域、コンソール/HTTPエラーを確認 — 3 projects passed。確認用 DB は一時DBで、終了後に削除しました。
- `scripts/shot.sh final` は3幅×7タブの21枚を取得し、画像を目視しました。録画監視ワーカー6個も常駐サービスを止めずに直接呼び出して例外が無いことを確認しました。

Windows サービスは依頼どおり停止・再起動していません。既存の `8000` 番サービスに対する最初の E2E では `/api/videos/count`、`/api/recording-rules`、`/api/settings/summary`、録画モニターの各 API が `404` となりました。現行コードを試験専用 DB・`8011` 番ポートで起動した再確認では全指定 API が `200`、Playwright は3幅すべて成功しました。したがって、既存サービスでは現行 backend API がまだ読み込まれていません。次回の計画されたサービス再起動後に再確認が必要です。本ラウンドで本番DBに適用した migration はなく、試験 DB は削除済みです。

これらの確認は実際の YouTube 配信を使った録画成功保証ではありません。自動テストのモック結果を実機検証として扱っていません。

### 未検証・未実装（理由）

- **faster-whisper 実推論**: 今回モデルのダウンロードと実録画推論は行っていません。モデル取得先・GPU/メモリを含む実機確認待ちです。
- **OpenRouter 実 API 呼び出し**: 実 API キーによる送信・応答確認はしていません。
- **HLS 追っかけ再生**: 実配信を使うブラウザー再生を確認していません。
- **ライブチャット取得**: 実配信での yt-dlp 取得・保存・同期を確認していません。
- **配信予定のチャンネル横断タイムライン**: 現状は配信予定一覧で、時間軸グリッドは未実装です。
- **復元可能なゴミ箱**: 未実装。Retention による自動削除とは完全に分離した復元機能が必要です。現行手動削除は復元できません。
- **通知、GUI ログ閲覧、録画ごとのエラーログ参照**: 未実装。エラー情報は backend ログと録画ディレクトリ親のログファイルで確認します。
- **変換キューの同時実行数/優先度/進捗/キャンセル GUI**: 変換処理と単一 worker はありますが、要件のキュー操作 UI は未実装です。変換元ファイルは保持します。
- **シリーズ/連番グルーピング**: 未実装。
- **簡易認証**: 未実装。現在は認証なしで、LAN/tailnet 内だけを信頼境界とします。
- **予約状態 `partial` / `conflict` を持つ独立予約モデル**: 録画動画の部分状態は保持しますが、独立した予約状態 enum は未実装です。
- **DB 内部キー自体の string 化**: 公開 API の ID 表現は文字列へ統一しましたが、PostgreSQL 内部主キーは整数です。DB の主キー型変更は既存データと全テーブルの参照移行が必要なため行っていません。
- **YouTube 実配信の検出、Remote 編集確認、Retention 削除**: 今回は実データを消さない方針のため、試験動画を用いた破壊的な実動作確認はしていません。削除ポリシーの単体テストと既存 smoke check で確認しています。

既知の注意点は、yt-dlp/YouTube の仕様変化による検出・Remote 確認失敗、そして本番配信の E2E が未実施であることです。Remote 確認不能は削除許可にせず保留にします。
