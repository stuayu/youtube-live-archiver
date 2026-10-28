# YouTube Live Archiver

複数の YouTube チャンネルを監視し、ライブ配信とアーカイブを保存するセルフホスト用アプリです。Backend は FastAPI / PostgreSQL、画面は Vue 3 / TypeScript で構成します。YouTube 取得に `yt-dlp`、メディア処理に `ffmpeg` と `ffprobe` を使います。

## セットアップと起動

ローカル実行は Python 3.11 または 3.12、Node.js / npm、`ffmpeg` が必要です。PostgreSQL と `yt-dlp` は Python 依存関係に含まれ、初回起動時に PostgreSQL を `backend/data/postgres` で自動起動します。録画ファイルの既定保存先は `backend/data/archive` です。

組み込み PostgreSQL（`pgserver`）の配布物は、Python 3.12 以前の Windows x64 / macOS / Linux x86_64 向けだけです。Python 3.13 以降や ARM 版 Linux では導入されないため、`backend/.env` の `DATABASE_URL` に外部の PostgreSQL を設定してください（未設定のまま起動すると、その旨を表示して終了します）。

### macOS / Linux（ローカル実行）

`ffmpeg` の導入例です。

```sh
# macOS
brew install ffmpeg
# Ubuntu / Debian
sudo apt update && sudo apt install ffmpeg
```

`python3 --version` が 3.11 または 3.12 であることを確認し（3.13 以降の場合は `python3.12 -m venv .venv` のようにバージョンを指定します）、リポジトリのルートで次を実行します。.env の作成は任意です。

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

### Windows（導入から常駐運用まで）

Windows 10 / 11 に新しく導入し、Windows サービスとして常駐させるまでの手順です。上から順に進めてください。コマンドは、「管理者 PowerShell」と書いた箇所以外は通常の PowerShell で実行します。例では導入先を `C:\yla` としています。

既に運用している環境を新しい版に上げる場合は、先に「[11. 旧版から更新する場合](#11-旧版から更新する場合)」を読んでください。

> **Windows での確認範囲**: Windows 11 x64 で新規導入と既存環境からの更新を確認しています。OS、Python、PostgreSQL、サービス構成ごとの確認状況と未検証項目は「[14. Windows での検証状況](#14-windows-での検証状況)」を参照してください。最初は「5. 起動して確認する」まで進め、手動起動で問題が無いことを確かめてからサービス化してください。

#### 1. 用意するもの

| 項目 | 内容 |
| --- | --- |
| OS | Windows 10 / 11 の 64 ビット版（x64）。ARM 版 Windows は、組み込み PostgreSQL の配布物が無いため対象外です。 |
| Python | **3.11（検証済み）または 3.12 の 64 ビット版**。python.org 版（winget の `Python.Python.3.11`）を使います。**Microsoft Store 版はサービスから実行できないため使いません。** 3.13 以降では組み込み PostgreSQL（`pgserver`）が導入されないため、外部の PostgreSQL が必要です。 |
| Node.js | 20 以上の LTS。画面のビルドに使います（実行時には不要です）。 |
| ffmpeg | `ffmpeg` と `ffprobe`。録画の結合・検証・変換に使います。 |
| Git | ソースコードの取得と更新に使います。 |
| ディスク | 本体と依存パッケージで約 1 GB、文字起こしを使う場合はモデル（既定の `large-v3-turbo` で約 1.6 GB）と、録画の保存先。 |
| NVIDIA GPU（任意） | Windows x64 の CUDA 12 対応 GPU。GPU ランタイムを追加する場合は約 1.3 GB の空き容量が必要です。 |

PostgreSQL と yt-dlp は、Python の依存パッケージとして一緒に入るため、別途インストールは不要です。

#### 2. 必要なソフトウェアを入れる

PowerShell を開き、次を 1 行ずつ実行します。

```powershell
winget install -e --id Git.Git
winget install -e --id Python.Python.3.11
winget install -e --id OpenJS.NodeJS.LTS
winget install -e --id Gyan.FFmpeg
```

`winget` が見つからない場合は、Microsoft Store で「アプリ インストーラー」を更新するか、各公式サイトのインストーラーを使ってください。

続けて、PowerShell のスクリプト実行を許可します（`npm` と `scripts\*.ps1` の実行に必要です。Windows の既定では禁止されています）。

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

**PowerShell を開き直してから**（PATH を反映するため）、すべてバージョンが表示されることを確認します。

```powershell
git --version
py -3.11 --version
node --version
npm --version
ffmpeg -version
ffprobe -version
```

#### 3. ソースコードを取得する

```powershell
git clone https://github.com/stuayu/youtube-live-archiver.git C:\yla
Set-Location C:\yla
```

- 導入先は `C:\yla` のように、**空白や日本語を含まない短いパス**にしてください（ユーザー名が日本語の場合、ユーザーフォルダー配下は避けます）。
- OneDrive で同期されるフォルダー（ドキュメント、デスクトップなど）には置かないでください。データベースと録画ファイルが常時書き換わります。
- ZIP でダウンロードした場合は、展開後に `Get-ChildItem C:\yla\scripts\*.ps1 | Unblock-File` を実行します（実行しないとスクリプトが「署名されていない」として止められます）。

#### 4. 依存パッケージを入れ、画面をビルドする

```powershell
Set-Location C:\yla\backend
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

Set-Location C:\yla\frontend
npm ci
npm run build
```

- 依存パッケージの導入には数分かかります。
- 仮想環境の有効化（`Activate.ps1`）は行わず、常に `.\.venv\Scripts\python.exe` を指定します。有効化し忘れによる取り違えを避けるためです。
- `npm run build` が終わると `C:\yla\frontend\dist` ができます。

#### 5. 起動して確認する

```powershell
Set-Location C:\yla\backend
.\.venv\Scripts\python.exe -m app
```

初回は、組み込み PostgreSQL の初期化とテーブル作成（マイグレーション）のため、待受開始まで数十秒かかります。次のように表示されれば起動完了です。

```text
組み込み PostgreSQL: C:\yla\backend\data\postgres
アプリデータ: C:\yla\backend\data
保存先ルート: C:\yla\backend\data\archive
http://0.0.0.0:8000/ で待受中
```

- 「Windows セキュリティの重要な警告」（ファイアウォール）が表示されたら、**「プライベート ネットワーク」だけにチェックを入れて「アクセスを許可する」**を選びます。「キャンセル」を選ぶと python.exe に対するブロック規則が作られ、後から他の端末で開けなくなります（対処は「13. Windows でよくあるつまずき」）。
- `警告: ffmpeg が見つかりません` などが出た場合は、手順 2 の確認コマンドをやり直してください。
- ブラウザーで `http://localhost:8000/` を開き、ホーム画面が表示されることを確認します。
- もう 1 つ PowerShell を開き、状態を確認します。

```powershell
Invoke-RestMethod http://localhost:8000/health                  # status : ok
(Invoke-RestMethod http://localhost:8000/api/status).database   # available : True
(Invoke-RestMethod http://localhost:8000/api/status).tools      # yt_dlp / ffmpeg / ffprobe がすべて True
```

確認できたら、下の「[初回設定](#初回設定)」に従って保存先とチャンネルを登録します。保存先には `D:\yla-archive` のようなローカルドライブのフォルダーを指定します。

終了は、起動した PowerShell で `Ctrl+C` を押します。録画中の配信がある場合は、録画を区切って保存してから終了します（最大 20 秒程度）。

#### 6. 設定ファイル（`backend\.env`）

既定のままで動くため、作成は任意です。待受ポート、保存先、データベースなどを変えるときだけ作ります。

```powershell
Copy-Item C:\yla\.env.example C:\yla\backend\.env
notepad C:\yla\backend\.env
```

- **置き場所は `backend\.env` です**（リポジトリ直下の `.env` は Docker Compose 用で、ローカル実行では読まれません）。
- 文字コードは UTF-8 で保存します（メモ帳の既定のままで構いません）。Windows PowerShell の `>` や `Out-File` で作ると UTF-16 になり、読み込めません。
- 変更を反映するには、アプリ（またはサービス）を再起動します。

よく変える項目は次のとおりです。全項目は `.env.example` のコメントを参照してください。

| 項目 | 既定値 | 内容 |
| --- | --- | --- |
| `APP_HOST` | `0.0.0.0` | 待受アドレス。この PC だけで使うなら `127.0.0.1` にします。 |
| `APP_PORT` | `8000` | 待受ポート。 |
| `LOG_LEVEL` | `INFO` | app logger のレベル。標準エラーに時刻・レベル・logger 名付きで記録します。 |
| `STORAGE_ROOT` | `./data/archive` | 既定の保存先。例: `D:/yla-archive` |
| `DATA_DIR` | `./data` | アイコン、チャット画像、配信予定サムネイル（`thumbnails`）、組み込み PostgreSQL の保存先。 |
| `REMOTE_IMAGE_FETCH_ENABLED` | `true` | 配信予定サムネイルを許可された画像ホストから取得する設定。`false` では未キャッシュ画像を取得しません。 |
| `YT_DLP_AUTO_UPDATE` | `true` | yt-dlp の最新版を自動確認し、選択チャンネルの更新を適用します。設定画面から変更できます。 |
| `YT_DLP_UPDATE_CHANNEL` | `nightly` | 初回設定の yt-dlp チャンネル（`stable` / `nightly` / `master`）。設定画面から切り替えできます。 |
| `YOUTUBE_IP_MODE` | `prefer_ipv6` | YouTube の IP。prefer_ipv6 は IPv6 到達性を確認して選択、ipv6 / ipv4 固定、auto は OS 任せ。 |
| `DATABASE_URL` | 空（組み込み PostgreSQL） | 外部の PostgreSQL を使う場合の接続 URL（手順 7）。 |
| `TRANSCRIPTION_ENABLED` | `true` | 文字起こし。GPU が無い PC では時間がかかるため、不要なら `false` にします。 |

Windows のパスは `D:/yla-archive` のようにスラッシュで書くのが安全です。`D:\yla-archive` も引用符で囲まなければそのまま使えますが、二重引用符で囲むと `\` がエスケープ文字として解釈されます。相対パスは `backend` フォルダーが基準です。

#### 7. データベースを選ぶ

**A. 組み込み PostgreSQL（既定）** — 追加の作業はありません。データは `backend\data\postgres` に保存され、アプリの起動・終了に合わせて起動・停止します。

**B. PostgreSQL 17 を別途インストールして使う** — 改修前から Windows サービスで運用してきた構成です。PostgreSQL が独立したサービスとして動き、`pg_dump` などの標準ツールでバックアップできます。

1. [postgresql.org の Windows 向けインストーラー](https://www.postgresql.org/download/windows/)で PostgreSQL 17 を入れます。設定は既定のまま、`postgres` ユーザーのパスワードを決め、ポートは `5432` にします。Windows サービス（既定の名前は `postgresql-x64-17`）として自動起動します。
2. アプリ用のデータベースを作ります（パスワードを聞かれます）。

   ```powershell
   & 'C:\Program Files\PostgreSQL\17\bin\createdb.exe' -U postgres yla
   ```

3. `backend\.env` に接続 URL を書きます。

   ```dotenv
   DATABASE_URL=postgresql+psycopg://postgres:パスワード@localhost:5432/yla
   ```

   パスワードに `@` `:` `/` `%` `#` を含む場合は URL エンコードします（例: `@` → `%40`、`#` → `%23`）。
4. 手順 5 と同じように起動します。テーブルは起動時に自動作成され、起動ログの 1 行目が `PostgreSQL: localhost:5432/yla` になります。接続できない場合は、30 秒再試行したあと原因を表示して終了します。

> **注意**: 運用を始めたあとで `DATABASE_URL` を空にしたり書き換えたりすると、別の（空の）データベースで起動し、登録済みのチャンネルや録画が画面から見えなくなります。データは元のデータベースに残っているので、設定を戻せば元に戻ります。組み込みから外部へ移す手順は「[データの保管と移行](#データの保管と移行)」を参照してください。

#### 8. Windows サービスとして常駐させる

PC の起動時に自動で立ち上がり、異常終了時に自動で再起動するようにします。**手順 5 の手動起動が成功していること**、および手動起動したアプリを `Ctrl+C` で終了してあること（同じポート・同じデータベースで二重に起動しないこと）を確認してから進めます。

1. サービス管理ツールの NSSM を入れます。

   ```powershell
   winget install -e --id NSSM.NSSM
   ```

2. **管理者 PowerShell を新しく開き**（スタートメニューで「PowerShell」を右クリック →「管理者として実行」）、登録スクリプトを実行します。

   ```powershell
   Set-Location C:\yla
   .\scripts\install-windows-service.ps1
   ```

   「スクリプトの実行が無効」と表示される場合は、`powershell -ExecutionPolicy Bypass -File .\scripts\install-windows-service.ps1` で実行します。

3. 動作を確認します。

   ```powershell
   Get-Service YouTubeLiveArchiver                      # Status が Running
   Invoke-RestMethod http://localhost:8000/health
   Get-Content C:\yla\logs\service-stdout.log -Tail 20
   ```

スクリプトが行う設定は次のとおりです。

- `backend\.venv\Scripts\python.exe -m app` を `YouTubeLiveArchiver` という名前のサービスに登録します（自動起動。異常終了時は 5 秒後に再起動）。
- 作業フォルダーは `backend`、ログは `logs\service-stdout.log` と `logs\service-stderr.log` です（起動のたびに古いログを別名で残します）。
- `backend\.env` の `DATABASE_URL` が同じ PC の PostgreSQL を指している場合は、その Windows サービス（名前が `postgresql` で始まるもの）の起動を待つよう依存関係を設定します。組み込み PostgreSQL の場合は設定しません。
- サービスの PATH に、`.venv\Scripts`（yt-dlp）と `%LOCALAPPDATA%\Microsoft\WinGet\Links`（winget で入れた ffmpeg）を加えます。
- 停止時は `Ctrl+C` 相当の信号を送り、録画の後始末のために最大 30 秒待ちます。

| オプション | 用途 |
| --- | --- |
| `-PythonPath <python.exe の絶対パス>` | 使う Python を明示します（仮想環境を別の場所に作った場合など）。 |
| `-DatabaseService <サービス名>` | 依存させる PostgreSQL のサービス名を明示します（複数入っている場合など。`Get-Service postgresql*` で確認できます）。 |
| `-UseScheduledTask` | NSSM を使わず、起動時のタスク（タスク スケジューラ）として登録します。 |

日常の操作は、管理者 PowerShell で行います。

```powershell
Stop-Service YouTubeLiveArchiver
Start-Service YouTubeLiveArchiver
Restart-Service YouTubeLiveArchiver    # backend\.env を変更したあとなど
```

サービスを停止・再起動すると、録画中の配信はそこで区切られ、起動後に自動で録画を再開します（別の区間として記録され、欠けた部分は配信終了後にアーカイブからの補完を試みます）。Python の場所やデータベースを変えたなど、登録内容そのものを変える場合は、`.\scripts\uninstall-windows-service.ps1` で解除してから登録し直します。

**サービス運用での注意**

- サービスは `LocalSystem` アカウントで動きます。ログインしているユーザーとは別の環境です。
  - Windows サービスの標準出力・標準エラーは `logs\service-stdout.log` / `logs\service-stderr.log` に記録されます。
  - 保存先はローカルドライブ（`D:\yla-archive` など）にします。エクスプローラーで割り当てたネットワークドライブ（`Z:` など）は、サービスからは見えません。NAS を使う場合は UNC パス（`\\nas\share\yla`）で登録し、共有にアクセスできるアカウントでサービスを動かします（`nssm set YouTubeLiveArchiver ObjectName .\ユーザー名 パスワード`。未検証）。
  - YouTube Cookieは設定画面からファイルを登録できます。チャンネルごとに別のCookieを使う場合、Cookie ProfileにNetscape形式のファイルパスを指定します（サービスからログイン中のユーザーのブラウザープロファイルは読めません）。
- `ffmpeg` と `ffprobe` は、サービスの PATH から見つかる必要があります（yt-dlp も PATH から探します）。winget 以外の方法で入れた場合は、そのフォルダーをシステム環境変数の `PATH` に追加してください。
- PC がスリープすると録画も止まります。常時稼働させる PC では、スリープと休止状態を無効にします。

  ```powershell
  powercfg /change standby-timeout-ac 0
  powercfg /change hibernate-timeout-ac 0
  ```

- Windows Update などで PC が再起動した場合は、サービスが自動で起動し、中断した録画を再開します。
- `-UseScheduledTask` で登録した場合は、PATH の追加とログファイルへの出力が行われず、停止（`Stop-ScheduledTask -TaskName 'YouTube Live Archiver'`）は強制終了になります。特別な理由が無ければ NSSM を使ってください。

**設定画面から依存関係をまとめて更新**

NSSM と LocalSystem で運用している場合は、設定画面の「依存関係の一括更新」を実行できます。新しい Python 環境と画面を別に準備し、録画・ダウンロード・文字起こし・要約・変換・バックグラウンド処理が終わってからサービスを切り替えます。新環境の起動確認に失敗した場合は、旧 Python 環境へ自動で戻します。切替中は設定変更などの更新 API を一時停止します。

- Python の `requirements.txt` と npm の宣言範囲に従い、範囲指定された依存関係を更新します。固定バージョンの Python パッケージと CUDA ライブラリは変更しません。
- yt-dlp は設定画面で選んだ stable / nightly / master のチャンネルに従います。録画中に実行中の yt-dlp はそのまま使い、更新版は次の起動から適用します。
- Python / Node.js 本体、固定版の CUDA ライブラリ、Deno、ffmpeg、PostgreSQL 本体、Windows や GPU ドライバーは対象外です。これらは Windows 側で個別に更新してください。
- 更新には新旧環境のための追加ディスク容量が必要です。切替後も現行と直前の Python 環境を残し、次回更新時に古い環境を整理します。
- NSSM 以外のサービス方式、対話ユーザーで動くサービス、npm がサービスの PATH から見つからない環境では利用できません。

#### 9. 他の端末から開く（ファイアウォールと Tailscale）

既定ではすべてのネットワークインターフェース（`0.0.0.0`）のポート 8000 で待ち受けますが、Windows のファイアウォールが受信を止めています。管理者 PowerShell で許可します。

```powershell
Set-Location C:\yla
.\scripts\setup-firewall.ps1                   # 同じ LAN（プライベート ネットワーク）からの接続を許可
.\scripts\setup-firewall.ps1 -AllowTailscale   # 加えて Tailscale（100.64.0.0/10）からの接続を許可
```

- ポートは `backend\.env` の `APP_PORT` から読み取ります（無ければ 8000）。`-Port 8010` のように明示もできます。ポートを変えたら再実行してください。
- LAN 側の規則は、接続中のネットワークが「プライベート」の場合だけ有効です。`Get-NetConnectionProfile` で `NetworkCategory` を確認し、`Public` になっている場合は、Windows の設定（ネットワークとインターネット → 接続のプロパティ）で「プライベート ネットワーク」に変更します。
- LAN 内の端末からは `http://<この PC の IP アドレス>:8000/`（IP アドレスは `ipconfig` で確認）、Tailscale 経由では `http://<Tailscale のマシン名>:8000/` で開きます。
- 規則を消すには、次を実行します。

  ```powershell
  Remove-NetFirewallRule -DisplayName 'YouTube Live Archiver TCP 8000'
  Remove-NetFirewallRule -DisplayName 'YouTube Live Archiver TCP 8000 Tailscale'
  ```

**このアプリには認証がなく、CORS は任意の origin を許可します。**信頼できる LAN または tailnet の中だけで使い、ルーターのポート開放や Tailscale Funnel などでインターネットへ公開しないでください。

#### 10. 更新する

1. バックアップを取ります。
   - 外部の PostgreSQL の場合（稼働中に取得できます）:

     ```powershell
     & 'C:\Program Files\PostgreSQL\17\bin\pg_dump.exe' -U postgres -d yla -Fc -f C:\yla-backup\yla.dump
     ```

   - 組み込み PostgreSQL の場合は、**サービスを停止してから** `C:\yla\backend\data\postgres` フォルダーをコピーします（稼働中のコピーは壊れたバックアップになります）。
2. 管理者 PowerShell でサービスを止めます。録画中の配信はここで区切られるため、可能なら録画が無い時間帯に行います。

   ```powershell
   Stop-Service YouTubeLiveArchiver
   ```

3. 通常の PowerShell で、コード・依存パッケージ・画面を更新します。

   ```powershell
   Set-Location C:\yla
   git pull
   .\backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
   Set-Location C:\yla\frontend
   npm ci
   npm run build
   ```

4. 管理者 PowerShell でサービスを起動します。データベースの変更（マイグレーション）は起動時に自動で適用されます。

   ```powershell
   Start-Service YouTubeLiveArchiver
   ```

5. `Get-Content C:\yla\logs\service-stdout.log -Tail 20` と、ホーム画面の「要対応」を確認します。

YouTube 側の仕様変更で録画やチャンネル確認が失敗する場合は、「設定」→「yt-dlp の更新」から更新します。yt-dlp 公式は通常利用者に `nightly` を推奨しているため、初期チャンネルも `nightly` です。`stable`、`nightly`、`master` を選び、自動更新の有効・無効を設定できます。自動更新は起動時と24時間ごとに確認します。

更新時は公式 GitHub リリースの実行ファイルをデータディレクトリの `tools/yt-dlp` に取得し、SHA-256 と起動後のバージョンを確かめてから切り替えます。実行中の録画・ダウンロードは完了まで続き、更新中は新しい処理の開始を待たせます。検証に失敗した場合は現行版を使い続け、直前版の実行ファイルも残します。「同じ版を強制再インストール」は、選択中のチャンネルの最新版を再取得します。

YouTube の完全な取得には Deno 2.3.0 以降と yt-dlp-ejs が必要です。Python 依存関係から導入する場合は `yt-dlp[default]` により EJS が入ります。設定画面に Deno と EJS の検出状態を別々に表示し、Deno 未導入時は[公式インストール手順](https://docs.deno.com/runtime/getting_started/installation/)を案内します。Bot 判定やログイン要求が続く場合は yt-dlp の更新だけで解消しないことがあります。

「設定」→「yt-dlp の更新」→「YouTube のCookie」から、[yt-dlp公式手順](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies)で書き出したNetscape形式のCookieファイルを選んで登録できます。YouTube以外のドメインのCookieは保存しません。Cookieは録画・アーカイブの動画ダウンロードにのみ使用し、定期監視、チャンネル登録時の確認、動画情報・形式の確認、チャット取得、保護判定には使用しません。チャンネルのCookie Profileに個別設定がある場合も同様です。個別設定がある場合は共通Cookieより優先します。登録・更新・削除は次の動画ダウンロードから反映され、実行中の録画は開始時のCookieを使い続けます。登録状態はYouTubeへのログイン成功を保証しません。匿名の定期監視がYouTubeに制限される場合は、Cookie登録後も監視や自動発見に失敗することがあります。

Cookieはログイン情報です。アプリへの通信には信頼できるLANまたはHTTPSを使い、ファイルや内容をチャット・ログ・共有資料へ貼り付けないでください。共通Cookieは `DATA_DIR/youtube-credentials` に保存し、Windowsではサービスの実行アカウント・SYSTEM・Administrators、Unixでは実行ユーザーにアクセスを制限します。アプリに認証はないため、アプリへ接続できる利用者を信頼できる範囲に限定してください。

Cookieを動画ダウンロード以外に送らないため、監視や情報確認のyt-dlp起動では外部のyt-dlp設定ファイルを読み込みません。そのため、外部設定に書いたプロキシなどの指定もこれらの処理には適用されません。

#### 11. 旧版から更新する場合

2026-10 の改修より前の版（PostgreSQL 17 と Windows サービスで運用している環境）から上げるときは、手順 10 に加えて次を確認します。

1. **`backend\.env` に `DATABASE_URL` が書かれていることを確認します。** 新しい版は、`DATABASE_URL` が空だと組み込み PostgreSQL（空のデータベース）で起動します。

   ```powershell
   Select-String -Path C:\yla\backend\.env -Pattern '^DATABASE_URL='
   ```

2. 必ず `pg_dump` でデータベースのバックアップを取ります（手順 10 の 1）。未適用のマイグレーションが起動時にまとめて適用されます。適用後のデータベースでは旧版は動かないため、旧版へ戻す場合はこのバックアップから復元します。
3. サービスが使っている Python を確認し、**その Python** に依存パッケージを入れます。旧版では仮想環境を使わず、pyenv-win などの Python に直接入れている場合があります。

   ```powershell
   nssm get YouTubeLiveArchiver Application
   & '<表示された python.exe のパス>' -m pip install -r C:\yla\backend\requirements.txt
   ```

   yt-dlp と EJS は依存パッケージに含まれます。`YT_DLP_PATH` の実行ファイルが見つからない場合は、同じ Python の yt-dlp を自動で使います。アプリ管理の更新実行ファイルを使う場合は、`YT_DLP_PATH` を既定値の `yt-dlp` にしてください。
4. 画面をビルドし直します（`npm ci` と `npm run build`）。
5. サービスを起動し、`http://localhost:8000/api/status` とログ、画面を確認します。
6. （任意）サービスを登録し直すと、停止時の待ち時間（30 秒）などの新しい設定が反映されます。`.\scripts\uninstall-windows-service.ps1` のあと `.\scripts\install-windows-service.ps1` を実行します。PostgreSQL のサービス名は自動で検出します。

新しい版で使わなくなった `.env` の項目は無視されます。追加された項目は `.env.example` を参照してください。

#### 12. アンインストール

管理者 PowerShell で、サービス（またはタスク）とファイアウォールの規則を消します。

```powershell
Set-Location C:\yla
.\scripts\uninstall-windows-service.ps1
Remove-NetFirewallRule -DisplayName 'YouTube Live Archiver TCP 8000' -ErrorAction SilentlyContinue
Remove-NetFirewallRule -DisplayName 'YouTube Live Archiver TCP 8000 Tailscale' -ErrorAction SilentlyContinue
```

そのあと `C:\yla` を削除します。**`C:\yla\backend\data` には組み込み PostgreSQL のデータと、既定の保存先（`data\archive`）の録画が入っています。**残したいものは先に退避してください。別の場所に登録した保存先のファイルと、外部の PostgreSQL のデータベースは、自動では消えません。

#### 13. Windows でよくあるつまずき

| 症状 | 対処 |
| --- | --- |
| `このシステムではスクリプトの実行が無効になっているため…`（`npm`、`scripts\*.ps1`） | 手順 2 の `Set-ExecutionPolicy` を実行します。`npm` は `npm.cmd ci` のように `npm.cmd` と書いても動きます。 |
| `python` と入力すると Microsoft Store が開く | Store 版への案内（エイリアス）です。`py -3.11` または `.\.venv\Scripts\python.exe` を使います。 |
| `py -3.11` で `No suitable Python runtime found` | Python 3.11 が入っていません。手順 2 を実行し、PowerShell を開き直します。 |
| 起動時に `DATABASE_URL が未設定です…` と表示されて終了する | 組み込み PostgreSQL（`pgserver`）が入っていません。Python が 3.13 以降、32 ビット版、ARM 版の場合は配布物がありません。64 ビット版の Python 3.11 で `.venv` を作り直すか、手順 7 の B で外部の PostgreSQL を使います。 |
| `警告: ffmpeg が見つかりません` | `winget install -e --id Gyan.FFmpeg` のあと、PowerShell を開き直します。サービスの場合は手順 8 の注意を確認します。 |
| ポートが使用中（エラー `10048`）で起動しない | 手動起動したアプリやサービスが既に動いていないか確認します。`Get-NetTCPConnection -LocalPort 8000 -State Listen` で使用中のプロセス ID を調べるか、`APP_PORT` を変更します。 |
| サービス登録で `依存パッケージ入りの python.exe が見つかりません` | 手順 4 が完了しているか確認します。Microsoft Store 版 Python で作った仮想環境は使えません。別の場所の Python を使う場合は `-PythonPath` で指定します。 |
| サービスが起動しない、すぐ止まる | `C:\yla\logs\service-stderr.log` と `service-stdout.log` の末尾を確認します。サービスを止めた状態で手順 5 の手動起動を行うと、同じエラーを画面で確認できます。 |
| 文字起こしが `open() got an unexpected keyword argument 'metadata_errors'` で失敗する | PyAV 19 以降は faster-whisper 1.2 と互換性がありません。`python -m pip install "av<19"`（サービスが使う Python で実行）のあと、サービスを再起動します。`requirements.txt` は `av<19` に固定済みです。 |
| この PC では開けるが、他の端末から開けない | `APP_HOST` が `127.0.0.1` になっていないか、手順 9 のスクリプトを実行したか、ネットワークが「プライベート」かを確認します。手動起動時の警告で「キャンセル」を選んだ場合は、「セキュリティが強化された Windows Defender ファイアウォール」の「受信の規則」から、python.exe に対するブロック規則を削除します。 |
| 登録したチャンネルや録画が消えたように見える | 別のデータベースで起動しています。`backend\.env` の `DATABASE_URL` と、起動ログの 1 行目を確認します（手順 7 の注意）。 |
| 夜間に録画が止まっている | PC のスリープ・休止状態を無効にします（手順 8 の注意）。 |

アプリ共通の症状は「[トラブルシューティング](#トラブルシューティング)」を参照してください。

#### 14. Windows での検証状況

Windows 11 x64 で、新規導入（Python 3.11、組み込み PostgreSQL、NSSM）と、既存環境からの更新（Python 3.13、PostgreSQL 17）を確認しました。`scripts\install-windows-service.ps1` は Windows PowerShell 5.1 でも確認しています。これは記載した構成での確認結果であり、全ての Windows 環境での動作を保証するものではありません。

Windows で未確認のもの:

- `scripts\setup-firewall.ps1` の実行、`-UseScheduledTask`（タスク スケジューラ）での登録。
- ライブ録画の手動停止（`CTRL_BREAK`）と部分録画の救出、録画中のサービス停止からの復帰。
- 文字起こし（faster-whisper）の実行。
- 外部の PostgreSQL を使う新規導入（手順 7 の B）の通し実行。更新した環境はこの構成です。

確認の中で分かった注意点:

- `py` ランチャーが無い環境（pyenv-win など）では、手順 4 の `py -3.11` の代わりに、Python 3.11 / 3.12 の `python.exe` を絶対パスで指定して仮想環境を作ります。
- PowerShell から `http://localhost:8000/` を呼ぶと、応答まで約 2 秒かかります（IPv6 を先に試すため）。確認コマンドは `http://127.0.0.1:8000/` にすると速くなります。ブラウザーでは問題になりません。
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

### ネットワークと公開範囲

このアプリには認証がなく、CORS は任意の origin を許可します。既定では全ネットワークインターフェースで待ち受けるため、信頼できる LAN / tailnet 内だけで使い、インターネットへ直接公開しないでください。この PC だけで使う場合は `APP_HOST=127.0.0.1` にします。録画、チャット、文字起こし、保存先情報などの取り扱いと公開前の確認は[プライバシーと公開前の確認](docs/PRIVACY_AND_RELEASE.md)を参照してください。Windows で他の端末から開くための設定は「Windows（導入から常駐運用まで）」の手順 9 を参照してください。

### 初回設定

1. 「ストレージ」で書き込み可能な保存先を登録し、必要なら既定の保存先にします。
2. 「チャンネル」で YouTube URL、`@handle`、チャンネル ID のいずれかを登録します。登録時に名前とアイコンを取得し、初回スキャンを実行します。
3. チャンネル設定で監視、保存先、保存期間、画質、Cookie Profile、チャット、追っかけ視聴などを調整します。

登録後のメタデータ更新はチャンネルの「情報更新」、今すぐの再スキャンは「今すぐ確認」を使います。

## 画面と主な操作

### URL を指定して追加

アーカイブまたは配信予定画面の「URL から追加」から、YouTube 動画 URL または 11 文字の動画 ID を入力して確認・追加できます。`youtube.com`（サブドメインを含む）と `youtu.be` の視聴、ライブ、Shorts URL に対応します。配信中はすぐ録画し、配信予定は開始を定期確認して録画、配信終了済み・通常動画はアーカイブをダウンロードします。未登録チャンネルは監視 OFF で登録します。手動の配信予定は開始 10 分前までは 30 分ごと、開始前 10 分から開始 2 時間後までは 1 分ごと、その後は 10 分ごとに確認します。登録済み動画は対象へ戻せる状態だけ再追加でき、それ以外は現在状態を表示して拒否します。

| 画面 | 主な操作 |
| --- | --- |
| ホーム | 監視・録画の概要、配信予定、最近の録画、システム状態を確認します。 |
| チャンネル | チャンネルを登録・編集・削除し、監視、次回確認時刻、予定数、録画数、失敗状態を確認します。削除は論理削除です。 |
| 配信予定 | 予定配信を検索・絞り込みし、録画予約の有効 / 無効を切り替えます。サムネイルはアプリが取得して `DATA_DIR/thumbnails` にキャッシュし、画面はアプリ経由で表示します。 |
| アーカイブ | 録画一覧を検索・絞り込み・並べ替えし、再生、ダウンロード、保護、削除、変換、録画再試行、停止を操作します。 |
| 全文検索 | 文字起こし本文を検索し、チャンネルや録画期間で絞り込みます。結果から録画の該当位置へ移動できます。 |
| ストレージ | 保存先の追加・編集・削除、既定先、使用量・空き容量を確認します。 |
| 分析 | チャンネル統計と録画量の推移を確認します。統計は取得開始後のスナップショットです。 |
| アクティビティ | 監視・録画・保護・Retention などの操作履歴と状態を確認します。 |
| 設定 | 表示、録画の実効設定、画質、録画ルール、文字起こし、要約、SNS 連携を設定します。 |

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

チャンネル登録直後に初回スキャンします。通常の監視間隔は6時間です。チャンネル設定の「時間帯ごとの監視間隔」で、指定時間に限り30分単位で頻度を上げられます（例: 18:00〜22:00を1時間、7:00〜8:00を30分）。時間帯は福島サーバーの現地時刻を使い、次の時間帯開始を飛び越さないよう次回確認を調整します。既存チャンネルも移行時に通常6時間へ変更されます。配信予定の開始前後は必要に応じて間隔を短縮します。

配信予定（`WAITING_LIVE`）がある場合は開始直前の確認を維持します。開始30分より前は通常間隔を使いつつ30分前までに確認し、30〜5分前は2〜4分、5分前〜10分後は20〜40秒、10〜60分後は1〜2分、1〜6時間後は5〜10分です。時刻不明の予定は10〜20分で確認します。固定間隔でも予定前後は短縮します。リクエスト回数を抑えるには、自動または長い固定間隔を選べます。チャンネル画面では固定間隔を一括で自動へ変更できます。

一時的なスキャン失敗は連続失敗数に応じて指数バックオフし、成功後にリセットします。アクセス制限を検出すると全チャンネルのスキャンをクールダウンし、連続検出時は10、20、40、最大60分へ延びます。

ホームとチャンネル画面では、監視中・確認待ち・停止中・録画中・エラーなどの健全性、直近の成功 / 失敗、次回確認時刻を表示します。システム状態と scheduler job の実行状況は `/api/status`、イベントは `/api/events` から確認できます。

## 録画と復旧

- 録画は `<保存先>/<チャンネル名>/<YYYY-MM-DD タイトル [動画ID]>/` に保存します。日付は予定開始時刻、なければ登録日時を使います。
  タイトルが空の場合は日付と動画 ID のみです。チャンネル名とタイトルは Windows で使えない文字と予約名を安全化し、既存録画の再開では登録済みフォルダーを継続使用します。
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

チャットリプレイは文字起こし・要約と独立した専用 worker が60秒間隔で処理し、1 tick につき1本取得します。取りこぼした録画は10分ごとに最大20本ずつ検出し、自動取得を5分刻みで間隔を空けて予約します。手動の「今すぐ再取得」は次の tick で優先します。取得間隔は通常1、3、6、12、以降24時間、最大10回です。アクセス制限時は試行回数を消費せず1時間後に再試行し、監視と共通のクールダウンへ入ります。

チャンネル監視の `AUTO` は過去90日の開始時刻を学習します。flat-playlist に開始時刻が無く学習用サンプルが12本未満なら、スキャンごとに inspect 枠を使って過去配信を1本ずつ調べます。URL から追加の確認サムネイルは `/api/videos/manual/thumbnail/<動画ID>` 経由で取得・キャッシュします。

チャットは通常コメント、スーパーチャット / スーパーステッカー、メンバーシップ、ギフト関連を保存します。絵文字画像は `DATA_DIR/chat-assets` にローカルキャッシュし、画面からはアプリ内のキャッシュ URL で読み込みます。キャッシュ済みの画像はデータベースを読まずにファイルから返します。未取得の画像は、チャット API が返した URL のものだけを取得し、取得に失敗した画像は10分間は再取得しません。コメントの再生時刻はライブ開始からの offset を録画開始 offset 分補正します。途中から録画した場合、録画より前のコメントは表示時刻をゼロへまとめず除外します。

アーカイブの「チャット再取得」は replay を取得してから取り込みます。取得不能時は既存コメントを維持し、バックグラウンドで再試行を予約する場合があります。「再解析」は保存済み JSONL を使ってコメントを再解析します。動画の再生位置に合わせてチャット一覧を追従します。

DPlayer の標準ホットキーを有効にしています。歯車メニューには再生速度、ループ、弾幕表示、不透明度、流れる速さ、文字サイズ、表示領域、弾幕の量、コメント種別（すべて / スーパーチャット / メンバーシップ）、削除済みコメントの表示設定があります。ライブ録画中はライブ追従と任意位置の追っかけを切り替えられます。追従設定時はライブ端から遅れた場合に追従位置へ戻ります。`d` キーで弾幕表示を切り替え、`Escape` キーで設定パネルを閉じるかプレイヤーを閉じます。設定の拡張は `frontend/src/lib/dplayerSettings.ts` で DPlayer の設定パネルへ項目を追加します。

文字起こしは「動画の再生位置に自動追従」を切り替えられます。ON のときは現在の発言を強調して一覧を追従させ、別ページの発言に移った場合も該当ページを読み込みます。OFF にすると自動追従を止めますが、発言を選んで動画を移動する操作は引き続き使えます。

### SNS タイムライン

再生画面の「タイムライン」から「SNS」を開くと、Bluesky と接続済み Misskey インスタンスのハッシュタグ検索、投稿ができます。検索は明示的に実行し、結果は投稿時刻順にまとめて表示します。サービスごとの失敗は別々に表示し、片方の検索エラーで他方の結果を隠しません。

先に「設定」→「SNS 連携」で接続します。サービスごとに複数アカウントを登録でき、登録済みアカウントを個別に解除できます。Bluesky は通常のアカウントパスワードではなくアプリパスワードを使います。Misskey はインスタンス URL を入力して「Misskeyと連携」を押し、開いた公式認証画面で権限を確認して許可します。アクセストークンの手入力は不要です。検索・投稿・画像添付のため read:account、read:notes、write:notes、write:drive を要求します。Misskey の検索アカウントも選択できます。認証情報の保存と外部送信の詳細は[プライバシーと公開前の確認](docs/PRIVACY_AND_RELEASE.md)を参照してください。

投稿画面では最大10アカウントから送信先を複数選び、本文、ハッシュタグ、Misskey の公開範囲を確認できます。同じ内容を複数の Bluesky / Misskey アカウントへクロスポストできます。YouTube 動画リンクは初期状態で含まれません。再生中の映像から JPEG キャプチャを最大4枚追加し、各画像の代替テキストを編集できます。キャプチャは長辺1280ピクセル・1枚2 MB以下へ変換され、投稿を送信するまで外部サービスへ渡りません。いずれかの送信先で失敗した場合は成功したアカウントの選択を外し、失敗先だけを再試行できます。

投稿の文字数は両サービスで使える300文字以内に制限します。Misskey の画像添付は write:drive を使い、投稿前に画像ファイルが Misskey 側へ保存されます。Bluesky の投稿は公開されます。投稿とキャプチャの扱いはサービス側の規約・公開設定に従ってください。

### ブラウザごとの再生方式

録画（主に MKV）は、開いているブラウザが再生できる形式を自動で選んで再生します。一覧の操作は常に「再生」で、形式を意識する必要はありません。

| 録画の形式 | Chrome / Edge（Chromium 系） | Safari（WebKit） |
| --- | --- | --- |
| MP4 / WebM で、コーデックをブラウザが再生できる | 元ファイルをそのまま再生 | 元ファイルをそのまま再生 |
| MKV で、H.264 + AAC など MP4 に入れればブラウザが再生できる | MP4 リマックスを自動で作って再生 | MP4 リマックスを自動で作って再生 |
| MKV で、AV1 + Opus など | MP4 リマックスを自動で作って再生 | H.264 変換を案内し、「変換して再生」を押すと変換して再生 |
| 形式を判定できない | 元ファイルを試し、読み込めなければ案内を表示 | 元ファイルを試し、読み込めなければ案内を表示 |

- 判定はブラウザの `canPlayType` で行うため、ほかのブラウザでも同じ規則で選びます。MKV は、再生できるブラウザがあっても直接は再生しません（ブラウザ間で挙動を揃えるため）。
- MP4 リマックスはコンテナの入れ替えだけで、画質は変わりません。H.264 変換は再エンコードのため、動画の長さや解像度に応じて時間がかかります。
- MP4 は長時間動画を Safari / WebKit で安定して再生できるよう、約1秒ごとにサンプルをチャンク化して出力します。
- チャンク形式を更新した起動時に、元録画が残っている場合だけ古い `.playable.mp4` / `.h264.mp4` を一度削除し、次回再生時に作り直します。
- 準備中・変換中の進捗と、読み込めなかった場合の案内（別の形式で再生 / 変換して再生 / ダウンロード / 再試行）は、動画の枠内に表示します。12秒以内にメタデータを読み込めない場合に案内を出し、遅れて読み込めた場合は案内を消します。ブラウザが自動再生を止めて一時停止のままになっている場合は、読み込めていれば失敗として扱いません。
- 再生中に動画情報の「MP4 リマックス」「H.264 変換」を押した場合は、再生を止めずに進捗だけを動画情報の欄に表示します。
- 変換元は録画の主ファイル（`recording.json` の `primary_media`）です。ライブ録画の区間とアーカイブ補完が同じフォルダーにある場合も、再生・ダウンロード・変換は同じファイルを対象にします。
- 録画中の追っかけ再生（HLS）は、Safari ではネイティブ再生、それ以外では hls.js を使います。
- コンテナ・コーデック・解像度は録画完了時に ffprobe で調べて保存します。既存の録画は、一覧 API が1回に最大5本、詳細 API が1本ずつ調べて保存します。

再生用に作った MP4（`.playable.mp4` / `.h264.mp4`）は、元ファイルから作り直せる派生物として扱います。最後のアクセスから `PLAYABLE_CACHE_DAYS` 日（既定14、`0` は削除しない）を過ぎたものを定期的に削除し、監査ログに `DERIVED_CLEANUP` として記録します。元ファイルには触れず、保護状態・容量集計・Retention・整合性確認の対象にも含めません。

## 文字起こし、要約、検索

録画完了後、任意設定で faster-whisper による文字起こしを処理キューへ登録します。失敗・再試行は録画状態から独立しています。文字起こしは動画詳細、全文検索、字幕出力から利用できます。要約は既定で無効で、OpenRouter を使う場合はタイムスタンプ付き文字起こしテキストを外部 API へ送ります。動画・音声ファイルは送信しません。

device が `auto` の場合は CUDA を試し、モデル初期化に失敗すると CPU / int8 で再試行します。GPU を使わず CPU で処理する場合は、設定画面で device を `cpu`、compute type を `int8` にします。処理中のジョブには設定変更は適用されません。

Windows で NVIDIA GPU を使う場合は、リポジトリのルートでサービス用 Python に任意の CUDA 依存パッケージを追加します（約 1.3 GB）。

```powershell
.\backend\.venv\Scripts\python.exe -m pip install -r .\backend\requirements-cuda-windows.txt
```

アプリは Windows の NVIDIA wheel に含まれる DLL ディレクトリを文字起こしモデルの読み込み前に登録します。Python から CUDA デバイスを検出すると `auto` が GPU を選びます。GPU のない環境ではこの追加依存は不要です。

## トラブルシューティング

| 症状 | 確認箇所 |
| --- | --- |
| `frontend/dist` が無い | `cd frontend && npm ci && npm run build` を実行します。起動は続きますが `/` は案内 HTML の 503 を返します。 |
| DB 接続エラー | 起動ログに表示された `host:port/database` と `backend/.env` の `DATABASE_URL` を確認します。外部 DB は既定30秒再試行します。`DATABASE_URL が未設定です` と表示される場合は組み込み DB（`pgserver`）が入っていません。Python 3.11 / 3.12 で仮想環境を作り直すか、外部 PostgreSQL の `DATABASE_URL` を設定します。 |
| ポート使用中（`Address already in use`） | `APP_PORT` を空いている番号へ変更して再起動します。 |
| ffmpeg が無い | macOS は `brew install ffmpeg`、Ubuntu / Debian は `sudo apt install ffmpeg`、Windows は `winget install -e --id Gyan.FFmpeg` を実行します。 |
| `/api/*` が DB 接続エラー | backend 上部に DB 切断を表示します。DB が復旧すると API 成功時に表示が消えます。 |
| チャンネル情報を取得できない | `yt-dlp --version`、ネットワーク、Cookie Profile、チャンネルのアクセス権を確認します。 |
| 配信が検出されない | チャンネル監視状態、`next_check_at`、最終成功 / エラー、システム `/api/status` を確認します。アクセス制限時はクールダウン終了を待ちます。 |
| 録画が始まらない | 録画枠、保存先の有効 / 書込権限 / 空き容量、`WAITING_FOR_STORAGE`、動画状態、`yt-dlp` ログを確認します。 |
| 録画が部分状態のまま | `recording.json`、録画ディレクトリの stem ごとのファイル、復旧履歴、YouTube 上の公開状態を確認します。配信終了時の yt-dlp エラーは、公開状態と YouTube / 実ファイルの長さが一致した場合だけ完了として扱います。保全ファイルを手動で消さないでください。 |
| 再生できない | プレイヤー内の案内に従い、リマックスまたは H.264 変換を選びます。 |
| 文字起こしされない | 設定の有効状態、処理ジョブ、ffmpeg、Whisper のモデル / device / compute type、ログを確認します。Windows で `cublas64_12.dll` が見つからない場合は、GPU を使うなら `requirements-cuda-windows.txt` をサービス用 Python にインストールしてサービスを再起動します。CPU を使うなら device=`cpu`、compute type=`int8` にします。 |
| Retention が保留される | Remote 状態、`remote_checks`、編集検出モード、ffmpeg、fingerprint と duration の取得状況を確認します。保留中のファイルは削除しません。 |

## テストと画面キャプチャ

`YLA_TEST_DATABASE_URL` を省略すると、backend pytest と Playwright E2E は pgserver を一時ディレクトリで起動し、終了時に片付けます。外部 PostgreSQL を使う場合は、`postgresql+psycopg://...` の専用テスト server（DB 作成権限あり）を指定してください。

Playwright E2E は Chromium の desktop / tablet / mobile と WebKit
（Desktop Safari 相当）で実行します。初回は
`npx playwright install chromium webkit` を実行してください。
AV1 / Opus シードは ffmpeg encoder が使える環境で生成します。

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

- 通知機能。
- 復元可能なゴミ箱。手動削除後のファイル復元機能はありません。
- 簡易認証。API と画面にログイン認証はありません。
- 要件で想定する数十〜数百チャンネルの長期運用の実証。

## 機能の検証範囲

Backend の unit / integration tests、frontend の unit tests、Playwright E2E を用意しています。E2E は隔離した一時 PostgreSQL とテスト用データを使い、Chromium の desktop / tablet / mobile と WebKit で画面を確認します。コマンドは「[テストと画面キャプチャ](#テストと画面キャプチャ)」、実配信を含む手順は [docs/E2E_TESTING.md](docs/E2E_TESTING.md) を参照してください。

自動テストの録画経路は fake の yt-dlp やメディア処理を使う箇所があり、実 YouTube の配信、各 OS の process stop、実データの再生や長期運用を保証しません。実配信確認は、自分が管理するテストチャンネルと隔離したデータベース・保存先で行ってください。

### 自動テストで代替できない確認

- Windows の録画停止イベント、部分ファイル救出、サービス停止後の復旧。
- 実配信での追っかけ視聴（ffmpeg tee）と配信先頭取得の自然終了。
- faster-whisper の実推論、OpenRouter の実 API 呼び出し。
- YouTube 上で非公開・削除・編集された動画の Retention 保護。
- 数十〜数百チャンネルの長期運用や、スーパーチャットを含む実チャットの表示。
