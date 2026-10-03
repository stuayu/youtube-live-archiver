# YouTube Live Archiver — 要件定義

## 1. 基本目的

複数の YouTube チャンネルを登録して定期監視し、ライブ配信を検出したら `yt-dlp` で自動保存する。

保存後は設定された保存期間に従って自動整理するが、次のいずれかに該当する場合は、ローカルに保存した元配信を保護対象へ自動昇格し、自動削除しない。

- YouTube 上で非公開になった
- YouTube 上から削除された
- アーカイブが視聴不能になった
- ライブ終了後に映像が編集・トリミングされた可能性がある

## 2. 全体構成

```
YouTube
   │
   ├── Channel Monitor
   │      ├ Channel A
   │      ├ Channel B
   │      ├ Channel C
   │      └ ...
   │
   ├── Live Detector
   │
   └── Remote Archive Inspector
             │
             ▼
       Backend / Scheduler
             │
     ┌───────┼────────┐
     ▼       ▼        ▼
 Recorder  Retention  Protection
 Worker     Worker      Worker
     │
     ▼
   yt-dlp
     │
     ▼
Custom Storage
```

### 技術スタック

```
Vue 3 + TypeScript   (GUI)
        │
        ▼
FastAPI              (Backend)
        │
        ▼
PostgreSQL           (DB)
```

## 3. 複数チャンネル登録

監視チャンネルは GUI から自由に追加・削除できる。登録方法は以下に対応する。

- YouTube Channel URL（例: `https://www.youtube.com/@example`）
- `@handle`（例: `@example`）
- `channel_id`（例: `UCxxxxxxxxxxxxxxxx`）

内部では必ず `channel_id` を正規 ID として保存する。

### チャンネル設定

チャンネルごとに以下を設定可能にする。

- チャンネル名
- YouTube Channel ID
- 監視 ON/OFF
- 通常監視間隔
- 保存先
- 保存期間
- 画質
- Cookie Profile
- 自動保護 ON/OFF
- 編集検出 ON/OFF

設定例（Channel A）:

```
監視       ON
通常監視   30分
保存先     NAS-01
保存期間   30日
自動保護   ON
編集検出   ON
```

## 4. チャンネル一覧 GUI

```
┌─────────────────────────────────────────────────────────┐
│ 監視チャンネル                         [+ チャンネル追加] │
├─────────────────────────────────────────────────────────┤
│ Channel A                                               │
│ ● Monitoring     次回確認 22:15                         │
│ 保存先 NAS-01    保存期間 30日                          │
│                                                         │
│ Channel B                                               │
│ ● Recording      LIVE                                   │
│ 保存先 HDD-02    保存期間 ∞                             │
│                                                         │
│ Channel C                                               │
│ ● Monitoring     次回確認 22:31                         │
│ 保存先 NAS-02    保存期間 90日                          │
└─────────────────────────────────────────────────────────┘
```

チャンネル数は数十〜数百を想定できる構成にする。

## 5. 監視間隔

通常時は低頻度監視とする。

| 状況 | 間隔 |
| --- | --- |
| 通常 | 30〜60分 |
| UPCOMING 発見 | 5〜10分 |
| 開始予定 30分以内 | 1〜3分 |
| 開始予定 5分以内 | 15〜30秒 |
| 録画開始後 | yt-dlp に引き渡し |

チャンネルごとに `30分` / `45分` / `60分` / `AUTO` から選択可能にする。

すべてのチャンネルを同時刻にチェックせず、jitter を入れる。

## 6. 保存先管理

保存先を固定しない。GUI に「ストレージ」という概念を作る。

登録例:

```
NAS-01     /mnt/nas/youtube
NAS-02     /mnt/archive/youtube
LOCAL-SSD  /data/youtube
USB-HDD    /media/archive
```

### Storage テーブル

```
id
name
root_path
enabled
minimum_free_space
warning_free_space
created_at
updated_at
```

## 7. 保存先をチャンネルごとに指定

```
Channel A → NAS-01
Channel B → NAS-02
Channel C → USB-HDD
```

さらに Default Storage も設定する。チャンネル側で未指定の場合は Default Storage を利用する。

## 8. 保存パス Template

ユーザーがパス形式を設定可能にする。

```
{channel_name}/{year}/{video_id}/
{channel_id}/{date}_{video_id}/
```

最終的な保存例:

```
/mnt/nas/youtube/
  Channel-A/
    2026/
      abc123XYZ/
        video.mkv
        video.info.json
        thumbnail.webp
        description.txt
        recording.json
        checksum.sha256
```

ファイルシステム上の安全性を考え、YouTube タイトルそのものを主キーにはしない。基本キーは `video_id` とする。

## 9. 保存期間

保存期間を設定可能にする。

```
永久保存 / 7日 / 14日 / 30日 / 60日 / 90日 / 180日 / 365日 / Custom
```

Custom の場合は `retention_days = N` を設定する。

基準日は **録画正常終了日時**。

例: 10月1日 22:00 録画完了、`retention_days = 30` なら 10月31日 22:00 以降に削除候補となる。ただし即削除はしない。

## 10. Retention 状態

動画ごとに以下を管理する。

```
ACTIVE
↓
EXPIRING
↓
VERIFYING_REMOTE
↓
DELETE_ELIGIBLE
↓
DELETED
```

保護された場合:

```
ACTIVE
↓
PROTECTED
```

`PROTECTED` は Retention Worker から完全に除外する。

## 11. N日経過後の処理

N 日経過したら、まず `DELETE` ではなく `VERIFY_REMOTE` を実行する。

```
保存からN日経過
      │
      ▼
YouTube状態確認
      │
      ├─ 公開中
      │      │
      │      ▼
      │   編集確認
      │      │
      │      ├─ 未編集
      │      │     ↓
      │      │ DELETE_ELIGIBLE
      │      │
      │      └─ 編集済み
      │            ↓
      │         PROTECTED
      │
      ├─ PRIVATE
      │      ↓
      │   PROTECTED
      │
      ├─ DELETED
      │      ↓
      │   PROTECTED
      │
      └─ 判定不能
             ↓
          PROTECTED_PENDING
```

**最重要ルール: YouTube 側を確認できない場合は削除しない。**

## 12. 非公開動画の自動保護

削除判定時に YouTube へアクセスして、以下のようにローカル録画から再取得できない状態になっていた場合、`protected = true` に変更する。

```
PRIVATE / UNAVAILABLE / REMOVED / DELETED / MEMBERS_ONLY / ACCESS_DENIED
```

理由も記録する（例: `protection_reason = REMOTE_PRIVATE`）。

GUI 表示例:

```
🔒 保護済み

理由:
YouTube側で非公開

ローカルコピー:
正常

録画日時:
2026/10/01

保護日時:
2026/10/31
```

## 13. ライブ編集検出

YouTube は「この動画はライブ終了後に編集された」という情報を常に明示的に返すわけではない。そのため複数情報を組み合わせて判定する。

### Level 1

録画終了時に以下を記録する。

```
original_duration
original_start_time
original_end_time
```

Retention 確認時に現在のアーカイブを再取得して `remote_duration` を比較する。

例: ローカルライブ `2:13:52` / 現在の YouTube `1:47:12` → `EDITED_TRIMMED` として保護。

## 14. Duration tolerance

YouTube 側の処理などによる数秒程度の差では保護しない。

```
duration_difference_threshold = 30秒
```

| 差 | 判定 |
| --- | --- |
| 3秒 | SAME |
| 12秒 | SAME |
| 2分 | EDITED |

閾値は設定可能にする。

## 15. より強い編集検出

Duration だけでは「途中をカットして別部分を追加し、最終時間がほぼ同じ」という編集を検出できない。そのため Optional として Content Fingerprint 機能を設ける。

録画完了後、`5% / 20% / 40% / 60% / 80% / 95%` 地点から数秒ずつ抽出して、映像 perceptual hash と音声 fingerprint を保存する。

```
fingerprints

00:10:00
video_hash = ...
audio_hash = ...

00:40:00
video_hash = ...
audio_hash = ...
```

削除判定時に YouTube 側の現在のアーカイブから同地点をサンプルして比較する。一定以上違えば `REMOTE_CONTENT_CHANGED` として保護する。

## 16. 編集検出モード

GUI から選択できるようにする。

```
編集検出
○ OFF
○ Basic
● Standard
○ Strong
```

- **Basic**: Duration のみ
- **Standard**: Duration + 複数ポイント映像 Fingerprint
- **Strong**: Duration + 映像 Fingerprint + 音声 Fingerprint

通常は Standard を推奨。

## 17. Metadata 変更は編集扱いにしない

タイトル変更・説明欄変更・Thumbnail 変更・カテゴリー変更だけでは保護しない。これらは映像本体の編集とは別扱いとする。

DB には変更履歴を保存してよいが、`protected = true` にはしない。

## 18. 保護理由

```
MANUAL
REMOTE_PRIVATE
REMOTE_DELETED
REMOTE_UNAVAILABLE
REMOTE_CONTENT_CHANGED
REMOTE_DURATION_CHANGED
REMOTE_CHECK_FAILED
LEGAL_HOLD
```

GUI では以下のように表示する。

```
🔒 PROTECTED
REMOTE_CONTENT_CHANGED
```

## 19. 判定不能時

DNS エラー、YouTube timeout、yt-dlp error、Network unreachable、HTTP 5xx などの場合、**絶対に削除しない。**

状態を `PROTECTION_PENDING` とし、再確認する。

```
1回目
↓
失敗

6時間後
↓
失敗

24時間後
↓
再確認
```

何回失敗しても自動削除には進めない。

## 20. 削除猶予期間

確認直後に削除するより、Deletion Grace Period を設けた方が安全。

例: `retention_days = 30`、`grace_days = 3` なら

```
30日経過
↓
YouTube確認
↓
DELETE_ELIGIBLE
↓
3日待機
↓
再確認
↓
削除
```

これなら誤判定にも強い。

## 21. 削除直前の最終確認

削除直前にもう一度 YouTube 側を確認する。

```
DELETE_ELIGIBLE
       │
       ▼
Final Remote Verification
       │
       ├─ PUBLIC + UNCHANGED
       │        ↓
       │      DELETE
       │
       └─ その他
                ↓
             PROTECTED
```

最低でも 2 回安全確認してから削除する。

## 22. 手動保護

GUI から動画単位で「🔒 永久保存する」を押せるようにする。

```
protected = true
protection_reason = MANUAL
```

チャンネル単位でも「このチャンネルは常に永久保存」を設定できる。

## 23. チャンネル Retention 設定例

```
Channel A

保存先            NAS-01
保存期間          30日
削除猶予          3日
非公開時          自動保護
削除時            自動保護
編集検出          Standard
Remote確認不能時  削除禁止
```

## 24. 動画一覧

```
┌───────────────────────────────────────────────────────────┐
│ Archive                                                   │
├───────────────────────────────────────────────────────────┤
│ 🔴 Channel A                                              │
│ 配信タイトル                                              │
│ 2026/10/01                                                │
│ 8.2 GB                                                    │
│                                                           │
│ 保存期限 2026/10/31                                       │
│ 状態 ACTIVE                                               │
├───────────────────────────────────────────────────────────┤
│ 🔒 Channel A                                              │
│ 過去ライブ                                                │
│ 2026/09/20                                                │
│ 12.1 GB                                                   │
│                                                           │
│ PROTECTED                                                 │
│ 理由: REMOTE_PRIVATE                                      │
├───────────────────────────────────────────────────────────┤
│ 🔒 Channel B                                              │
│ ライブ                                                    │
│                                                           │
│ PROTECTED                                                 │
│ 理由: REMOTE_CONTENT_CHANGED                              │
└───────────────────────────────────────────────────────────┘
```

## 25. DB スキーマ

### videos

```
id
youtube_video_id
channel_id
title
recorded_at
recording_finished_at
original_duration
remote_status
retention_days
retention_expires_at
protected
protection_reason
protected_at
deletion_candidate_at
deletion_after
created_at
updated_at
```

### storages

```
id
name
root_path
minimum_free_bytes
enabled
created_at
updated_at
```

### channels

```
id
youtube_channel_id
name
enabled
poll_interval
storage_id
retention_days
deletion_grace_days
edit_detection_mode
auto_protect_private
auto_protect_deleted
auto_protect_edited
created_at
updated_at
```

### remote_checks

```
id
video_id
checked_at
remote_status
remote_duration
edit_detected
error
raw_metadata
```

### fingerprints

```
id
video_id
position
video_hash
audio_hash
```

## 26. Storage 安全チェック

保存開始前に以下を行う。

- 保存先存在確認
- write test
- disk free 確認

保存先 NAS が切断されている場合、録画成功扱いには絶対にしない。状態を `WAITING_FOR_STORAGE` とするか、Emergency Storage へ切り替える。

Optional: Primary Storage（NAS）/ Fallback Storage（Local HDD）にも対応可能とする。

## 27. ファイル削除

削除対象になった場合、いきなり DB レコードまで消さない。

`video.mkv` / thumbnail / metadata / fingerprints など実ファイルを削除する。DB 上には `DELETED_BY_RETENTION` として履歴を残す。

保持する情報:

```
Video ID
録画日
削除日
保存期間
削除時のRemote状態
```

後から「この動画録画したことあった？」を確認できるようにする。

## 28. Audit Log

特に削除はすべて記録する。

```
2026-11-03 03:02

VIDEO:        abc123
ACTION:       DELETE
REASON:       Retention expired
REMOTE:       PUBLIC
EDIT CHECK:   UNCHANGED
RETENTION:    30 days
GRACE:        3 days
```

保護も記録する。

```
ACTION:  PROTECT
REASON:  REMOTE_PRIVATE
```

## 29. 基本的なライフサイクル

30日保存の場合:

```
10/01 LIVE開始
↓
録画
↓
ARCHIVED
↓
10/31 Retention期限
↓
Remote Check → YouTube PUBLIC
↓
Edit Check → UNCHANGED
↓
DELETE_ELIGIBLE
↓
3日Grace
↓
11/03 Remote再確認
↓
PUBLIC / UNCHANGED
↓
ローカル削除
```

10/31 の Remote Check が PRIVATE だった場合:

```
PRIVATE
↓
PROTECTED
↓
永久保存
```

編集されていた場合:

```
YouTube    1:54:32
Local Live 2:13:42
↓
REMOTE_CONTENT_CHANGED
↓
PROTECTED
```

## 30. 最重要設計原則

本システムでは、

> **削除してよいことが確認できた動画だけ削除する**

という設計にする。

逆に、

> 削除してはいけないことが確認できなかったから削除する

という設計には**絶対にしない**。

したがって、次の条件がすべて成立した場合だけ自動削除する。

- PUBLIC
- UNCHANGED
- Remote 確認成功
- Retention 期限超過
- Grace 期間超過

それ以外は `KEEP` または `PROTECTED` とする。これをシステム全体の Retention Policy とする。
