param([switch]$UseScheduledTask, [string]$PythonPath, [string]$DatabaseService)
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw '管理者 PowerShell で実行してください。'
}
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$backend = Join-Path $root 'backend'

# 探索用にネイティブコマンドを実行し、成否と標準出力を返す。
# Windows PowerShell 5.1 では ErrorActionPreference が Stop のまま標準エラーを
# リダイレクトすると、stderr への出力が終了エラーになるため、この関数内だけ Continue にする。
function Invoke-Probe([string]$exe, [string[]]$arguments) {
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $exe @arguments 2>$null
        return @{ Ok = ($LASTEXITCODE -eq 0); Output = $output }
    } catch {
        return @{ Ok = $false; Output = $null }
    }
}

# Microsoft Store の App Execution Alias (WindowsApps\python.exe) は
# LocalSystem から実行できないため、実体のインタプリタを解決する。
# 管理者シェルではユーザーの PATH に pyenv のシムが載っていないことがあるため、
# 既知のインストール先も含めて順に探索する。-PythonPath で明示指定も可能。
function Test-RealPython([string]$path) {
    if (-not $path) { return $false }
    if ($path -like '*\WindowsApps\*') { return $false }
    if (-not (Test-Path $path)) { return $false }
    # 依存が入っているインタプリタかどうかまで確認する。
    $probe = Invoke-Probe $path @('-c', 'import sys, sqlalchemy, fastapi, uvicorn; print(sys.base_prefix)')
    if (-not $probe.Ok) { return $false }
    # 仮想環境の元が Microsoft Store 版 Python の場合も LocalSystem から実行できない。
    return -not ("$($probe.Output)" -like '*\WindowsApps\*')
}

# .env から 1 つの値を読む（同じキーが複数あれば最後の行。前後の空白と引用符は外す）。
function Get-EnvValue([string]$file, [string]$key) {
    if (-not (Test-Path $file)) { return $null }
    $pattern = '^\s*' + [regex]::Escape($key) + '\s*=(.*)$'
    $value = $null
    foreach ($line in (Get-Content -LiteralPath $file -Encoding UTF8)) {
        if ($line -match $pattern) { $value = $Matches[1].Trim().Trim('"').Trim("'") }
    }
    return $value
}

$candidates = @()
if ($PythonPath) { $candidates += $PythonPath }
# README の手順で作る仮想環境 (backend\.venv) を優先する。
$candidates += (Join-Path $backend '.venv\Scripts\python.exe')
$candidates += ((Invoke-Probe 'python.exe' @('-c', 'import sys; print(sys.executable)')).Output | Select-Object -First 1)
$candidates += (Get-Command python.exe -All -ErrorAction SilentlyContinue | ForEach-Object { $_.Source })
foreach ($base in @("$env:USERPROFILE\.pyenv\pyenv-win\versions", "$env:LOCALAPPDATA\Programs\Python", 'C:\Program Files')) {
    if (Test-Path $base) {
        $candidates += (Get-ChildItem $base -Directory -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            ForEach-Object { Join-Path $_.FullName 'python.exe' })
    }
}

$python = $candidates | Where-Object { $_ } | Select-Object -Unique | Where-Object { Test-RealPython $_ } | Select-Object -First 1
if (-not $python) {
    $looked = ($candidates | Where-Object { $_ } | Select-Object -Unique) -join "`n  "
    throw ("依存パッケージ入りの python.exe が見つかりません（Microsoft Store 版 Python は使えません）。" +
        "-PythonPath で絶対パスを指定してください。`n探索した候補:`n  $looked")
}
Write-Host "Python: $python"

# 同じ PC の PostgreSQL へ接続する設定のときだけ、その Windows サービスへの依存を設定する。
# 組み込み PostgreSQL（DATABASE_URL が空または embedded）や別ホストの DB には依存先が無い。
# サービス名はインストール方法で変わる（例: postgresql-x64-17）ため、-DatabaseService で明示指定もできる。
$databaseUrl = Get-EnvValue (Join-Path $backend '.env') 'DATABASE_URL'
$localDatabase = $databaseUrl -and ($databaseUrl -match '^[^:]+://([^@/]*@)?(localhost|127\.0\.0\.1|\[::1\])(:\d+)?([/?]|$)')
if (-not $DatabaseService -and $localDatabase) {
    $DatabaseService = Get-Service -Name 'postgresql*' -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending | Select-Object -First 1 -ExpandProperty Name
}
if ($DatabaseService -and -not (Get-Service -Name $DatabaseService -ErrorAction SilentlyContinue)) {
    Write-Warning "サービス '$DatabaseService' が見つからないため、依存関係は設定しません。"
    $DatabaseService = $null
}

$logs = Join-Path $root 'logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$stdout = Join-Path $logs 'service-stdout.log'
$stderr = Join-Path $logs 'service-stderr.log'
$nssm = Get-Command nssm.exe -ErrorAction SilentlyContinue
if (-not $UseScheduledTask -and $nssm) {
    $name = 'YouTubeLiveArchiver'
    & $nssm.Source install $name $python '-m app'
    & $nssm.Source set $name AppDirectory $backend
    & $nssm.Source set $name AppStdout $stdout
    & $nssm.Source set $name AppStderr $stderr
    & $nssm.Source set $name AppRotateFiles 1
    & $nssm.Source set $name Start SERVICE_AUTO_START
    if ($DatabaseService) {
        & $nssm.Source set $name DependOnService $DatabaseService
        Write-Host "依存サービス: $DatabaseService"
    } else {
        Write-Host '依存サービス: なし（組み込み PostgreSQL、または別ホストの DB）'
    }
    & $nssm.Source set $name AppExit Default Restart
    & $nssm.Source set $name AppRestartDelay 5000
    # 停止時は Ctrl+C を送り、最大 30 秒待つ（NSSM の既定は 1.5 秒）。録画の後始末
    # (RECORDER_SHUTDOWN_TIMEOUT_SECONDS、既定 20 秒) と組み込み PostgreSQL の停止を待つため。
    & $nssm.Source set $name AppStopMethodConsole 30000
    # LocalSystem はユーザーの PATH を引き継がないため、外部ツールの場所を明示的に足す。
    $extraPath = @(
        (Split-Path $python -Parent),
        (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links')
    ) -join ';'
    & $nssm.Source set $name AppEnvironmentExtra "PATH=$extraPath;%PATH%"
    Start-Service $name
    Write-Host "サービス $name を登録・起動しました。"
    Write-Host "ログ: $stdout"
} else {
    if (-not $UseScheduledTask) { Write-Warning 'NSSM が見つかりません。winget install -e --id NSSM.NSSM を実行して新しい管理者 PowerShell で再試行するか、-UseScheduledTask を指定してください。' }
    $taskName = 'YouTube Live Archiver'
    $action = New-ScheduledTaskAction -Execute $python -Argument '-m app' -WorkingDirectory $backend
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest -User 'SYSTEM' -Force | Out-Null
    Start-ScheduledTask -TaskName $taskName
    Write-Host "起動時タスク '$taskName' を登録しました。"
}
