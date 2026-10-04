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

> **検証状況**: この節は、スクリプトとコードの動作に合わせて書いていますが、2026-10 の改修では Windows 実機で通し実行していません（同じ流れは macOS で確認済みです）。未確認の点は「[14. Windows での検証状況](#14-windows-での検証状況)」にまとめています。最初は「5. 起動して確認する」まで進め、手動起動で問題が無いことを確かめてからサービス化してください。

#### 1. 用意するもの

| 項目 | 内容 |
| --- | --- |
| OS | Windows 10 / 11 の 64 ビット版（x64）。ARM 版 Windows は、組み込み PostgreSQL の配布物が無いため対象外です。 |
| Python | **3.11（検証済み）または 3.12 の 64 ビット版**。python.org 版（winget の `Python.Python.3.11`）を使います。**Microsoft Store 版はサービスから実行できないため使いません。** 3.13 以降では組み込み PostgreSQL（`pgserver`）が導入されないため、外部の PostgreSQL が必要です。 |
| Node.js | 20 以上の LTS。画面のビルドに使います（実行時には不要です）。 |
| ffmpeg | `ffmpeg` と `ffprobe`。録画の結合・検証・変換に使います。 |
| Git | ソースコードの取得と更新に使います。 |
| ディスク | 本体と依存パッケージで約 1 GB、文字起こしを使う場合はモデル（既定の `large-v3-turbo` で約 1.6 GB）と、録画の保存先。 |

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
| `STORAGE_ROOT` | `./data/archive` | 既定の保存先。例: `D:/yla-archive` |
| `DATA_DIR` | `./data` | アイコン、チャット画像、組み込み PostgreSQL の保存先。 |
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
  - 保存先はローカルドライブ（`D:\yla-archive` など）にします。エクスプローラーで割り当てたネットワークドライブ（`Z:` など）は、サービスからは見えません。NAS を使う場合は UNC パス（`\\nas\share\yla`）で登録し、共有にアクセスできるアカウントでサービスを動かします（`nssm set YouTubeLiveArchiver ObjectName .\ユーザー名 パスワード`。未検証）。
  - Cookie Profile には、ブラウザー名ではなく Netscape 形式の Cookie ファイルのパスを指定します（サービスからはブラウザーのプロファイルを読めません）。
- `ffmpeg` と `ffprobe` は、サービスの PATH から見つかる必要があります（yt-dlp も PATH から探します）。winget 以外の方法で入れた場合は、そのフォルダーをシステム環境変数の `PATH` に追加してください。
- PC がスリープすると録画も止まります。常時稼働させる PC では、スリープと休止状態を無効にします。

  ```powershell
  powercfg /change standby-timeout-ac 0
  powercfg /change hibernate-timeout-ac 0
  ```

- Windows Update などで PC が再起動した場合は、サービスが自動で起動し、中断した録画を再開します。
- `-UseScheduledTask` で登録した場合は、PATH の追加とログファイルへの出力が行われず、停止（`Stop-ScheduledTask -TaskName 'YouTube Live Archiver'`）は強制終了になります。特別な理由が無ければ NSSM を使ってください。

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

YouTube 側の仕様変更で録画やチャンネル確認が失敗するようになった場合は、yt-dlp だけを更新してサービスを再起動します。

```powershell
C:\yla\backend\.venv\Scripts\python.exe -m pip install -U yt-dlp
```

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

   yt-dlp は依存パッケージに含まれるようになりました。`YT_DLP_PATH` の実行ファイルが見つからない場合は、同じ Python の yt-dlp を自動で使います。
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
| この PC では開けるが、他の端末から開けない | `APP_HOST` が `127.0.0.1` になっていないか、手順 9 のスクリプトを実行したか、ネットワークが「プライベート」かを確認します。手動起動時の警告で「キャンセル」を選んだ場合は、「セキュリティが強化された Windows Defender ファイアウォール」の「受信の規則」から、python.exe に対するブロック規則を削除します。 |
| 登録したチャンネルや録画が消えたように見える | 別のデータベースで起動しています。`backend\.env` の `DATABASE_URL` と、起動ログの 1 行目を確認します（手順 7 の注意）。 |
| 夜間に録画が止まっている | PC のスリープ・休止状態を無効にします（手順 8 の注意）。 |

アプリ共通の症状は「[トラブルシューティング](#トラブルシューティング)」を参照してください。

#### 14. Windows での検証状況

- 2026-10 の改修（監視・録画・画面の作り直し、組み込み PostgreSQL、自動マイグレーション）は macOS 上で行い、Windows 実機では実行していません。
- Windows で未確認のもの: この節の手順の通し実行、組み込み PostgreSQL（`pgserver` の Windows 版）での動作、サービス（`LocalSystem`）配下での組み込み PostgreSQL、NSSM の停止待ち時間、`CTRL_BREAK` による録画停止と部分録画の救出。
- `scripts\install-windows-service.ps1` と `scripts\setup-firewall.ps1` は今回修正しました（仮想環境の Python を優先、組み込み PostgreSQL では依存サービスを設定しない、`backend\.env` のポートを読む）。構文と判定ロジックは macOS 上の PowerShell 7 で確認しましたが、Windows PowerShell 5.1 での実行、NSSM・サービス・ファイアウォールの操作は未確認です。
- 改修前の版は、Windows サービス + PostgreSQL 17 の構成で運用されていました。確実さを優先する場合は、手順 7 の B（外部の PostgreSQL）を選んでください。

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

このアプリには認証がなく、CORS は任意の origin を許可します。信頼できる LAN / tailnet 内で使い、インターネットへ直接公開しないでください。この PC だけで使う場合は `APP_HOST=127.0.0.1` にします。Windows で他の端末から開くための設定は「Windows（導入から常駐運用まで）」の手順 9 を参照してください。

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

チャットは通常コメント、スーパーチャット / スーパーステッカー、メンバーシップ、ギフト関連を保存します。絵文字画像は `DATA_DIR/chat-assets` にローカルキャッシュし、画面からはアプリ内のキャッシュ URL で読み込みます。キャッシュ済みの画像はデータベースを読まずにファイルから返します。未取得の画像は、チャット API が返した URL のものだけを取得し、取得に失敗した画像は10分間は再取得しません。コメントの再生時刻はライブ開始からの offset を録画開始 offset 分補正します。途中から録画した場合、録画より前のコメントは表示時刻をゼロへまとめず除外します。

アーカイブの「チャット再取得」は replay を取得してから取り込みます。取得不能時は既存コメントを維持し、バックグラウンドで再試行を予約する場合があります。「再解析」は保存済み JSONL を使ってコメントを再解析します。動画の再生位置に合わせてチャット一覧を追従します。

DPlayer の標準ホットキーを有効にしています。歯車メニューには再生速度、ループ、弾幕表示、不透明度、流れる速さ、文字サイズ、表示領域、弾幕の量、コメント種別（すべて / スーパーチャット / メンバーシップ）、削除済みコメントの表示設定があります。ライブ録画中はライブ追従と任意位置の追っかけを切り替えられます。追従設定時はライブ端から遅れた場合に追従位置へ戻ります。`d` キーで弾幕表示を切り替え、`Escape` キーで設定パネルを閉じるかプレイヤーを閉じます。設定の拡張は `frontend/src/lib/dplayerSettings.ts` で DPlayer の設定パネルへ項目を追加します。

### ブラウザごとの再生方式

録画（主に MKV）は、開いているブラウザが再生できる形式を自動で選んで再生します。一覧の操作は常に「再生」で、形式を意識する必要はありません。

| 録画の形式 | Chrome / Edge（Chromium 系） | Safari（WebKit） |
| --- | --- | --- |
| MP4 / WebM で、コーデックをブラウザが再生できる | 元ファイルをそのまま再生 | 元ファイルをそのまま再生 |
| MKV で、H.264 + AAC など MP4 に入れればブラウザが再生できる | MP4 リマックスを自動で作って再生 | MP4 リマックスを自動で作って再生 |
| MKV で、AV1 + Opus など | MP4 リマックスを自動で作って再生 | H.264 変換を案内し、「変換して再生」を押すと変換して再生 |
| 形式を判定できない | 元ファイルを試し、読み込めなければ案内を表示 | 元ファイルを試し、読み込めなければ案内を表示 |

- 判定はブラウザの `canPlayType` で行うため、ほかのブラウザでも同じ規則で選びます。MKV は、再生できるブラウザがあっても直接は再生しません（ブラウザ間で挙動を揃えるため）。
- MP4 リマックスはコンテナの入れ替えだけで、画質は変わりません（実測: 2時間4分・581 MB で約36秒）。H.264 変換は再エンコードで、時間がかかります（実測: 64分・640x360 で約2分15秒。解像度が高いほど長くなります）。
- 準備中・変換中の進捗と、読み込めなかった場合の案内（別の形式で再生 / 変換して再生 / ダウンロード / 再試行）は、動画の枠内に表示します。12秒以内にメタデータを読み込めない場合に案内を出し、遅れて読み込めた場合は案内を消します。ブラウザが自動再生を止めて一時停止のままになっている場合は、読み込めていれば失敗として扱いません。
- 再生中に動画情報の「MP4 リマックス」「H.264 変換」を押した場合は、再生を止めずに進捗だけを動画情報の欄に表示します。
- 変換元は録画の主ファイル（`recording.json` の `primary_media`）です。ライブ録画の区間とアーカイブ補完が同じフォルダーにある場合も、再生・ダウンロード・変換は同じファイルを対象にします。
- 録画中の追っかけ再生（HLS）は、Safari ではネイティブ再生、それ以外では hls.js を使います。
- コンテナ・コーデック・解像度は録画完了時に ffprobe で調べて保存します。既存の録画は、一覧 API が1回に最大5本、詳細 API が1本ずつ調べて保存します。

再生用に作った MP4（`.playable.mp4` / `.h264.mp4`）は、元ファイルから作り直せる派生物として扱います。最後のアクセスから `PLAYABLE_CACHE_DAYS` 日（既定14、`0` は削除しない）を過ぎたものを定期的に削除し、監査ログに `DERIVED_CLEANUP` として記録します。元ファイルには触れず、保護状態・容量集計・Retention・整合性確認の対象にも含めません。

## 文字起こし、要約、検索

録画完了後、任意設定で faster-whisper による文字起こしを処理キューへ登録します。失敗・再試行は録画状態から独立しています。文字起こしは動画詳細、全文検索、字幕出力から利用できます。要約は既定で無効で、OpenRouter を使う場合はタイムスタンプ付き文字起こしテキストを外部 API へ送ります。動画・音声ファイルは送信しません。

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
| 録画が部分状態のまま | `recording.json`、録画ディレクトリの stem ごとのファイル、復旧履歴、YouTube 上の公開状態を確認します。保全ファイルを手動で消さないでください。 |
| 再生できない | プレイヤー内の案内に従い、リマックスまたは H.264 変換を選びます。 |
| 文字起こしされない | 設定の有効状態、処理ジョブ、ffmpeg、Whisper のモデル / device / compute type、ログを確認します。 |
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
- 実録画の WebKit での再生（Playwright の WebKit 26.6。macOS 実機の Safari ではありません）: H.264 + AAC の MKV（2時間4分）を自動リマックスして再生・シーク・弾幕表示。AV1 + Opus の MKV（64分）で変換の案内 → 「変換して再生」→ 進捗表示 → H.264 で再生・シーク。再生用ファイルが準備済みの場合は、一覧を開いた直後に再生しても約0.5〜1秒で読み込み完了（チャット 26,555 件の DB。修正前は絵文字画像の取得が1件40〜50秒かかり、動画の読み込みが30秒以上待たされていた）。

### 自動テスト

最終のローカル実行結果（2026-10-04、macOS）は次のとおりです。

- backend pytest: 314 passed（指定 PostgreSQL URL。`YLA_TEST_DATABASE_URL` 無指定では、絵文字画像の修正前の 312 件で成功を確認）
- frontend format:check: 成功
- frontend lint: 成功
- frontend build: 成功
- frontend test:unit: 47 passed
- frontend E2E: 68 passed / 16 skipped（Chromium の PC / tablet / mobile と WebKit。skip は対象外のプロジェクトで実行しない再生シナリオ）
- 初回起動 smoke: 新規 venv で成功。README 手順、組み込み DB、migration、画面、yt-dlp、保存先登録を確認
- frontend shots: review 78枚を生成。WebKit の準備中・再生中・変換の案内・読み込み失敗の案内を目視確認
- 依存パッケージの解決: Windows x64 の Python 3.11〜3.13、Linux x86_64 / ARM64 の Python 3.12、macOS の Python 3.11〜3.13 を対象に、`requirements.txt` の全パッケージを配布物（wheel）だけで解決できることを確認（Python 3.13 と ARM64 Linux では `pgserver` を除く。実際の導入は macOS の Python 3.11 のみ）
- Windows 用スクリプト: macOS 上の PowerShell 7 で構文と判定ロジック（Python の探索、`.env` の読み取り、同じ PC の PostgreSQL の判定、ポートの解決）を確認

### 未検証（理由）

- Windows 実行: Windows 環境を今回使っていないため。導入手順の通し実行、組み込み PostgreSQL、CTRL_BREAK による停止、部分録画救出、サービス運用、修正した PowerShell スクリプトの実行を含みます（「Windows（導入から常駐運用まで）」の「14. Windows での検証状況」）。
- macOS / iOS 実機の Safari での再生: Playwright の WebKit で確認した範囲です。実機の自動再生制限は、再生を拒否される状況を模した E2E で確認しています。
- 先頭から録画した配信の自然終了までの一連動作: 実機確認はユーザー停止までの範囲です。
- 追っかけ視聴の ffmpeg tee 実配信: 実配信での動作を確認していません。
- faster-whisper の実推論: 実モデルで確認していません。
- OpenRouter 実 API: 外部 API 呼び出しを確認していません。
- 非公開・削除・編集動画に対する Retention 保護: 実 YouTube での保護動作は未検証です。単体・統合テストのみです。
- 数十〜数百チャンネルの長期運用: 規模・期間を満たす稼働を確認していません。
- スーパーチャットを含む実チャット表示: 通常チャット等の確認と分けて、実スーパーチャットを確認していません。
