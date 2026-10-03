# 実配信 E2E 検証

この手順はテスト用チャンネルと短い限定公開ライブで実施してください。録画・Remote 確認には YouTube への通信、yt-dlp、ffmpeg が必要です。

## 事前準備

1. `.env` の PostgreSQL 接続先と `ARCHIVE_PATH` を設定し、`docker compose up -d db`。
2. backend 環境で `alembic upgrade head` を実行。`alembic current` が head を示すことを確認。
3. `yt-dlp --version`、`ffmpeg -version` を確認。Cookie が必要な配信は Netscape 形式 Cookie ファイルを backend から読める位置に用意し、チャンネルの Cookie Profile にそのパスを設定する。
4. backend を起動し、`GET /health` が `{"status":"ok"}` を返すことを確認。ログは `channel monitor failed`、`recording failed`、`retention file removal failed` を検索する。

## 配信検出から録画完了

1. UIまたは `POST /api/storages` で書込可能な保存先を登録し、`POST /api/channels` でテストチャンネルを登録。編集検出は Standard、保存期間は通常値にする。`GET /api/channels` で enabled と正規 `UC...` ID を確認。
2. テストライブを UPCOMING として作成する。最大通常監視間隔の範囲で待ち、`GET /api/videos` または SQL で `WAITING_LIVE` を確認する。

   ```sql
   SELECT youtube_video_id, status, raw_metadata FROM videos ORDER BY created_at DESC LIMIT 10;
   ```

3. 予定開始30分以内では1〜3分、5分以内では15〜30秒程度へ確認間隔が縮む。スケジューラログと `channels.next_check_at` を確認する。
4. 配信開始後、動画が `DISCOVERED` を経て `RECORDING` となり、保存先に video ファイルが作られることを確認。終了後 `ACTIVE`、`recording_finished_at`、`retention_expires_at` が設定される。

   ```sql
   SELECT youtube_video_id, status, recorded_at, recording_finished_at, original_duration, file_path
   FROM videos ORDER BY created_at DESC LIMIT 10;
   SELECT v.youtube_video_id, f.position, f.video_hash, f.audio_hash
   FROM fingerprints f JOIN videos v ON v.id=f.video_id
   ORDER BY v.created_at DESC, f.position;
   ```

   Standard は位置 `0.05, 0.2, 0.4, 0.6, 0.8, 0.95` の6行、Strong は各行に音声 hash もあること。ログに fingerprint エラーがないこと。

## Retention と保護動作

安全確認は必ず隔離したテスト動画で行い、実データのファイルパスを対象にしないでください。時間を進めず、対象テスト動画の `retention_expires_at` を過去にし、初回Remote確認後の `deletion_after` を過去に書き換えると2段階確認を短時間で試せます。

```sql
UPDATE videos SET retention_expires_at = now() - interval '2 days' WHERE youtube_video_id = '<TEST_VIDEO_ID>';
-- 初回確認後に実行
UPDATE videos SET deletion_after = now() - interval '1 minute' WHERE youtube_video_id = '<TEST_VIDEO_ID>' AND status='DELETE_ELIGIBLE';
SELECT id, status, remote_status, protection_reason, deletion_after FROM videos WHERE youtube_video_id='<TEST_VIDEO_ID>';
SELECT checked_at, remote_status, edit_detected, error FROM remote_checks WHERE video_id=(SELECT id FROM videos WHERE youtube_video_id='<TEST_VIDEO_ID>') ORDER BY checked_at;
SELECT created_at, action, reason FROM audit_logs WHERE video_id=(SELECT id FROM videos WHERE youtube_video_id='<TEST_VIDEO_ID>') ORDER BY created_at;
```

初回は `DELETE_ELIGIBLE`、成功した独立したRemote確認が2回未満なら削除されないこと、2回目も PUBLIC/未変更なら `DELETED_BY_RETENTION` と実ファイル削除が記録されることを確認します。`GET /api/videos` でも状態を確認。

保護テストでは保持期限を過去にし、確認ごとに `remote_checks` と `videos.status/protection_reason` を確認します。

- 初回確認の前に配信アーカイブを限定公開または非公開へ変更し、REMOTE 状態が対応する非公開値なら `PROTECTED` / `REMOTE_PRIVATE` を確認。
- YouTube Studio でアーカイブを短く編集し、公開状態のまま保持期限を過去にする。duration または fingerprint 差で `PROTECTED`（`REMOTE_DURATION_CHANGED` / `REMOTE_CONTENT_CHANGED`）になることを確認。
- Remote 確認中だけネットワークを遮断する。確認失敗が `remote_checks.error` に記録され、`PROTECTION_PENDING` / `REMOTE_CHECK_FAILED` になり、ファイルが残ることを確認。判定不能は `PROTECTED` ではなく再試行可能な保留状態。

失敗・成功いずれも次で追跡できます。

```sql
SELECT youtube_video_id, status, protected, protection_reason, file_path FROM videos WHERE youtube_video_id='<TEST_VIDEO_ID>';
SELECT checked_at, remote_status, remote_duration, edit_detected, error FROM remote_checks WHERE video_id=(SELECT id FROM videos WHERE youtube_video_id='<TEST_VIDEO_ID>') ORDER BY checked_at DESC;
```

API は `GET /api/videos`、チャンネル設定は `GET /api/channels`。確認不能・指紋不足・削除失敗のどれでもファイルが消えないことを最後に確認します。
