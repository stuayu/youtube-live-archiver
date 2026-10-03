---
name: yla-codex
description: YouTube Live Archiver の実装を codex へ委譲するときの起動方法。サンドボックス制限で npm run build やスクリーンショットが権限エラーになる問題の回避フラグを含む。「codex に投げる」「実装委譲」「codex で実装」で使う。
---

# codex 委譲の起動方法

## 正しい起動コマンド

```bash
codex exec --yolo --dangerously-bypass-hook-trust \
  -m gpt-6-luna --skip-git-repo-check \
  -C "C:/prog/youtube-live-archiver" - < プロンプトファイル
```

`--yolo` と `--dangerously-bypass-hook-trust` を**必ず付ける**。

## reasoning effort は config.toml 側で決める

`-c model_reasoning_effort=...` をコマンドラインで渡すな。
**Windows で前回値に引っ張られる不具合報告がある。** `~/.codex/config.toml` に書く。

```toml
model = "gpt-6-luna"
model_reasoning_effort = "high"
approval_policy = "never"
sandbox_mode = "danger-full-access"

[projects.'C:\prog']
trust_level = "trusted"
```

GPT-6 Luna の effort は `none / low / medium / high / xhigh / max`。既定 medium。

- 普段の実装: medium
- バグ調査・大きめのリファクタ: high
- 難しい設計や解析: xhigh
- 品質最優先: max

Luna は高効率モデルなので**常用は high**。現在の設定も high。

## なぜ必要か

付けないと codex 側で以下が全部権限エラーになり、検証が一切できない:

- `npm run build` → `spawn EPERM`（esbuild の子プロセス起動が弾かれる）
- `bash scripts/shot.sh` → `CreateFileMapping` アクセス拒否（Git Bash が起動できない）
- `pytest` の一時ディレクトリ → Windows の Temp ACL が読めず収集失敗
- `yt-dlp` → `[PYI] Failed to create parent directory structure`（PyInstaller の展開先が作れない）

これで実配信 E2E が丸ごと失敗した実績がある。

## その他の必須フラグ

- `--skip-git-repo-check` — このリポジトリは git 管理外。無いと
  `Not inside a trusted directory` で即終了する。
- `-C <ディレクトリ>` — 作業ディレクトリ指定。
- `- < ファイル` — プロンプトは stdin で渡す。引数で渡すと長文が壊れる。

## プロンプトは stdin、ヒアドキュメントは使うな

プロンプトファイルは **Write ツールで作れ**。Bash のヒアドキュメントで
書こうとすると、権限で弾かれた場合にファイルが作られず、
**stdin が空のまま codex が即終了する**（実際に起きた。exit 0 で何もせず終わる）。

## 複数ラウンドは1本のスクリプト内で逐次実行

並走させると同じファイルを別プロセスが同時編集して壊れる。
過去に `App.vue` が3プロセスに同時編集されて構文破壊した。

待機ループに `pgrep` を使うな。**この Git Bash に pgrep は無い。**
コマンドが毎回エラーになって条件が偽になり、待たずに次が走る（これが事故の原因）。

正しいやり方 — 同一シェル内で順番に呼ぶ:

```bash
for p in prompt16.txt prompt17.txt prompt18.txt; do
  codex exec --yolo --dangerously-bypass-hook-trust ... - < "$SP/$p"
done
```

## 委譲後は必ず自分で検証する

codex の自己申告を鵜呑みにするな。`yla-verify` スキルの手順を回す。
特に `npm run build` は codex が通せなかった場合でもこちらで必ず回す。
過去に見逃したビルドエラー:

- 未終端文字列 `{{m.is_deleted?'[削除済] ':'}}` → `vue-tsc` は通るが Vite で落ちる
- `import 'dplayer/dist/DPlayer.min.css'` → DPlayer 1.27 は CSS を JS に同梱しており
  単体 CSS が存在しない
