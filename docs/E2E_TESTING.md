# E2E 検証

この文書は、frontend の Playwright E2E、画面キャプチャ、実 YouTube 配信での運用確認を分けて説明します。実配信確認には自分が管理するテストチャンネルと短い配信を使い、通常利用中の保存先・動画を操作しないでください。

## 自動テスト

### Backend

Backend pytest は `YLA_TEST_DATABASE_URL` が無い場合、pgserver を一時ディレクトリで起動し、`yla_test_` prefix の一時 DB とともに終了時に片付けます。外部 PostgreSQL を使う場合は、`postgresql+psycopg://...` の専用テスト server（DB 作成権限あり）を指定してください。

Windows PowerShell:

```powershell
cd backend
python -m pytest -q -p no:cacheprovider
```

macOS / Linux:

```sh
cd backend
python -m pytest -q -p no:cacheprovider
```

`YLA_TEST_DATABASE_URL` を指定するときは、作成権限のある隔離 PostgreSQL サーバーを使います。実運用 DB を指定しないでください。

### Frontend format / lint / build / unit / E2E / shots

Windows PowerShell:

```powershell
cd frontend
npm ci
npm run format:check
npm run lint
npm run build
npm run test:unit
npm run test:e2e
npm run shots
```

macOS / Linux:

```sh
cd frontend
npm ci
npm run format:check
npm run lint
npm run build
npm run test:unit
npm run test:e2e
npm run shots
```

E2E は Playwright の desktop 1440、tablet 820、mobile 390 幅で navigation、channels、upcoming、archive、search、storage、analysis、activity、settings、player 等を確認します。既定ではテスト用 PostgreSQL と Vite preview/backend を起動し、`YLA_E2E_URL` を指定すると指定 URL に接続します。shots は light / dark と3幅で画面を `frontend/shots/latest/` に保存します。両方ともテスト用データを使います。

## 実配信の確認

実配信検証は自動テストでは代替できません。backend を専用 DB とテスト保存先で起動し、`yt-dlp --version`、`ffmpeg -version`、`ffprobe -version` を確認します。Cookie が必要な場合は Netscape cookie file または backend から利用できる browser profile を channel 設定に指定します。画面の `/api/status` 表示、backend log、動画状態、ファイル生成を合わせて確認します。

### 登録と初回スキャン

1. 「ストレージ」で専用の書込先を登録します。API を使う場合は `POST /api/storages`、確認は `GET /api/storages` と `GET /api/storages/usage` です。
2. 「チャンネル」でテストチャンネル URL / `@handle` / ID を登録します。API は `POST /api/channels`、一覧は `GET /api/channels` です。
3. 登録後すぐにスキャンが走ること、名前・アイコン、`last_success_at`、`next_check_at` と監視健全性が更新されることを確認します。「今すぐ確認」は `POST /api/channels/{channel_id}/check-now` です。
4. YouTube 上の live / upcoming / 過去配信が、画面の「配信予定」「アーカイブ」に対応する状態で表示されることを確認します。予定は `WAITING_LIVE`、録画 queue は `DISCOVERED` です。

### 予定配信から録画

1. upcoming item の予定開始時刻が UI と `GET /api/videos` に現れることを確認します。monitor は予定時刻が近づくと確認間隔を短縮します。
2. 配信開始後に状態が `DISCOVERED`、次に `RECORDING` となり、動画 directory に stem media が増えることを確認します。`GET /api/videos/{id}/recording/monitor` で進行中状態を確認できます。
3. 録画中は `recording.json` の attempt / stem、heartbeat、別 process の chat ファイルを確認します。チャンネル設定で chat と追っかけ視聴を有効にした場合も、対応する process と HLS が現れるか確認します。
4. 自然終了後、`ACTIVE` または補完待ちの `DISCOVERED`、`archive_backfill`、`is_partial`、primary media、checksum、`recording.json` を照合します。先頭欠け・複数区間を補完した場合、完全な archive が primary になり、部分フラグが解除されることを確認します。
5. 完了後、アーカイブ画面から再生し、チャット offset がローカル再生時刻に合うこと、検索・絞り込み・再解析・export を確認します。チャット API は `GET /api/videos/{id}/chat`、再取得は `POST /api/videos/{id}/chat/retrieve`、再解析は `POST /api/videos/{id}/chat/reparse` です。

### 停止と再起動復旧

1. 録画中に UI の停止、または `POST /api/videos/{id}/recording/stop` を実行します。プロセス内の子プロセスだけが停止対象です。
2. 救出できたユーザー停止は `ACTIVE`、`is_partial=true`、`user_stopped=true` を確認します。再生可能な部分ファイルと `recording.json` が残り、自動再開しないことを確認します。出力を救出できない停止は `RECORDING_FAILED` になり、成果物が保全されることを確認します。
3. アプリ終了時は録画中であることを確認してから専用検証環境で行います。再起動後、公開中の配信は別 stem で復旧し、終了済みは archive retrieval が queue されることを確認します。非公開 / 削除 / 判定不能は保護または retry wait になり、ファイルが保持されることを確認します。

### Retention と安全性

Retention は隔離したテスト動画だけで実施します。実運用 DB や重要録画の期限を SQL で書き換えないでください。初回確認後の `DELETE_ELIGIBLE` / `deletion_after`、最終 Remote 確認、audit log、ファイル削除後の `DELETED_BY_RETENTION` を確認します。非公開、削除、編集、通信失敗、duration / fingerprint 不足のケースでは `PROTECTED` または `PROTECTION_PENDING` となり、ファイルが残ることを確認します。`is_partial=true` の行は初回判定・最終削除とも対象外です。

Retention の実配信確認には実際の期限到来またはテスト環境内の制御された時間経過を使います。運用 DB に対する手動 SQL による期限短縮は行いません。削除 API は `DELETE /api/videos/{id}` で、手動削除後の復元可能な trash はありません。

## 結果記録

実配信検証では日付、OS、yt-dlp / ffmpeg version、チャンネルと配信の状態、UI 操作、動画 status、`archive_backfill` / `is_partial` / `user_stopped`、保存ファイル名、確認できた範囲を記録します。未実施の OS、配信経路、外部 API、長期負荷試験は「未検証」として理由を添えます。
