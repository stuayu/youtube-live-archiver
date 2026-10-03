---
name: yla-verify
description: YouTube Live Archiver の変更後に必ず回す検証手順。pytest・マイグレーション・フロントビルド・全画面キャプチャ（PC/タブレット/スマホ）・録画監視の生存確認・モック混入チェックまで。「検証して」「バグ潰し」「スクショ確認」「リリース前チェック」で使う。
---

# YLA 検証手順

コード変更後、これ全部通るまで「できた」と言うな。

## 1. backend テスト

```bash
cd C:/prog/youtube-live-archiver/backend
python -m pytest -q -p no:cacheprovider
```

全件 pass 必須。失敗1件でも次へ進むな。

## 2. マイグレーション

```bash
cd C:/prog/youtube-live-archiver/backend
python -m alembic upgrade head
python -m alembic current
```

- head に到達必須。
- 新規DBでの `upgrade head` → `downgrade base` も確認。本番DB `yla` は壊すな。
- ALTER がハングしたら `pg_stat_activity` で `idle in transaction` を疑え。

## 3. frontend ビルド

```bash
cd C:/prog/youtube-live-archiver/frontend
npm run build
```

**`npx vue-tsc -b` だけでは不十分。** 型チェックは通るのに Vite のテンプレート
パーサで落ちるケースが実際にあった（未終端文字列 `:'}}`）。必ず `npm run build` まで回す。

## 4. 全画面キャプチャ

```bash
cd C:/prog/youtube-live-archiver
bash scripts/shot.sh <ラベル>
```

PC 1440 / タブレット 820 / スマホ 390 の3幅 × 全タブを `shots/` に出力。
撮ったら**必ず画像を開いて目視**しろ。撮っただけで終わるな。

見る点:
- 横スクロールが出ていないか（スマホ幅で input がはみ出す事故が過去にあった）
- ラベル無しの入力欄が無いか（「30」「0」だけ出てて意味不明、の事故あり）
- タップ対象が小さすぎないか（44x44px 相当）
- ライト/ダーク両方で読めるか
- タブ切替・リロードで状態が飛ばないか

**ヘッドレス Chrome の `--window-size` は Windows のウィンドウ最小幅に
引きずられる。** 狭い幅は `frame.html` の iframe 経由で撮れ。直接撮ると
「はみ出している」という誤判定をする（実際に誤報を出した）。

## 5. API 生存確認（モック化・壊れ検出）

```bash
for e in health api/channels api/videos api/storages api/storages/usage api/quality-presets; do
  printf "%s: " "$e"; curl -s -m 8 -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:8000/$e"
done
```

全部 200 必須。500 が出たらモデルとDBのカラム不一致（マイグレーション未適用）を疑え。

**モックが本番コードに混入していないか確認:**

```bash
grep -rn "mock\|dummy\|FIXME\|TODO\|NotImplemented" backend/app/ --include=*.py | grep -v tests
```

テスト以外に出てきたら潰す。

## 6. 録画監視が生きているか（最重要）

GUI が綺麗でも監視が死んでいたら無価値。実際に `Channel.created_at` 欠落で
チャンネル監視が毎回例外死し、録画も検出もゼロだった事故がある。

```bash
# ワーカーの例外をログで確認
tail -40 C:/prog/youtube-live-archiver/logs/service-stderr.log

# 次回確認時刻が進んでいるか
PGPASSWORD=ylapass "/c/Program Files/PostgreSQL/17/bin/psql.exe" -h localhost -U postgres -d yla \
  -c "SELECT id,name,enabled,next_check_at FROM channels;"

# 動画の状態分布
PGPASSWORD=ylapass "/c/Program Files/PostgreSQL/17/bin/psql.exe" -h localhost -U postgres -d yla \
  -c "SELECT status,count(*) FROM videos GROUP BY status;"
```

- `next_check_at` が止まっていたら監視が死んでいる。
- ワーカーを直接叩いて例外を見る:

```bash
cd C:/prog/youtube-live-archiver/backend
printf 'from app.workers import channel_monitor\nchannel_monitor()\nprint("ok")\n' > _chk.py
python _chk.py; rm -f _chk.py
```

- 実配信で検出まで確認したいなら、DB の `next_check_at` を過去にして即発火させる:

```sql
UPDATE channels SET next_check_at = now() - interval '1 minute';
```

## 7. サービス反映

backend のコード変更は**サービス再起動しないと反映されない**。静的ファイル
（`frontend/dist`）は再起動不要。

```
Restart-Service YouTubeLiveArchiver
```

管理者 PowerShell が必要。自分では実行できないのでユーザーに依頼しろ。
**進行中の録画があるなら再起動は中断を招く。** 必ず確認してから。

## 絶対に破るな

- 要件30章: 削除してよいと確認できた動画だけ削除する。
- 録画の失敗は削除の理由にならない。過去に `rmtree` で 1.44GB 消した事故あり。
- Remote 確認の失敗を「存在しない」と解釈するな。保留に倒せ。
- 部分録画しか無い状態で自動削除するな。
- 中間ファイル（`*.part` / `*.ytdl`）は最終成果物の存在とサイズ>0 を確認してからのみ消せ。
