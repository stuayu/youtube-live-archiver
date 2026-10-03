param([switch]$UseScheduledTask, [string]$PythonPath)
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw '管理者 PowerShell で実行してください。'
}
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$backend = Join-Path $root 'backend'
# Microsoft Store の App Execution Alias (WindowsApps\python.exe) は
# LocalSystem から実行できないため、実体のインタプリタを解決する。
# 管理者シェルではユーザーの PATH に pyenv のシムが載っていないことがあるため、
# 既知のインストール先も含めて順に探索する。-PythonPath で明示指定も可能。
function Test-RealPython([string]$path) {
    if (-not $path) { return $false }
    if ($path -like '*\WindowsApps\*') { return $false }
    if (-not (Test-Path $path)) { return $false }
    # 依存が入っているインタプリタかどうかまで確認する。
    & $path -c "import sqlalchemy, fastapi, uvicorn" 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

$candidates = @()
if ($PythonPath) { $candidates += $PythonPath }
$candidates += (& python.exe -c "import sys; print(sys.executable)" 2>$null | Select-Object -First 1)
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
    throw "依存パッケージ入りの python.exe が見つかりません。-PythonPath で絶対パスを指定してください。`n探索した候補:`n  $looked"
}
Write-Host "Python: $python"
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
    & $nssm.Source set $name DependOnService postgresql-17
    & $nssm.Source set $name AppExit Default Restart
    & $nssm.Source set $name AppRestartDelay 5000
    # LocalSystem はユーザーの PATH を引き継がないため、外部ツールの場所を明示的に足す。
    $extraPath = @(
        (Split-Path $python -Parent),
        (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links')
    ) -join ';'
    & $nssm.Source set $name AppEnvironmentExtra "PATH=$extraPath;%PATH%"
    Start-Service $name
    Write-Host "サービス $name を登録・起動しました。"
} else {
    if (-not $UseScheduledTask) { Write-Warning 'NSSM が見つかりません。winget install nssm を実行し、再試行するか -UseScheduledTask を指定してください。' }
    $taskName = 'YouTube Live Archiver'
    $action = New-ScheduledTaskAction -Execute $python -Argument '-m app' -WorkingDirectory $backend
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest -User 'SYSTEM' -Force | Out-Null
    Start-ScheduledTask -TaskName $taskName
    Write-Host "起動時タスク '$taskName' を登録しました。"
}
