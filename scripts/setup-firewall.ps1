param([switch]$AllowTailscale, [int]$Port)
$ErrorActionPreference = 'Stop'
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw '管理者 PowerShell で実行してください。' }
# 待受ポートは -Port、backend\.env（アプリが読む設定）、リポジトリ直下の .env（旧配置）の順に決める。
$listenPort = 8000
if ($Port) {
    $listenPort = $Port
} else {
    foreach ($envFile in @((Join-Path $PSScriptRoot '..\backend\.env'), (Join-Path $PSScriptRoot '..\.env'))) {
        if (-not (Test-Path $envFile)) { continue }
        $line = Get-Content $envFile | Where-Object { $_ -match '^\s*APP_PORT\s*=' } | Select-Object -Last 1
        if ($line -and $line -match '^\s*APP_PORT\s*=\s*(\d+)\s*$') {
            $listenPort = [int]$Matches[1]
            break
        }
    }
}
$name = 'YouTube Live Archiver TCP ' + $listenPort
Remove-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue
Remove-NetFirewallRule -DisplayName ($name + ' Tailscale') -ErrorAction SilentlyContinue
$profiles = 'Private'
if ($AllowTailscale) {
    New-NetFirewallRule -DisplayName ($name + ' Tailscale') -Direction Inbound -Action Allow -Protocol TCP -LocalPort $listenPort -RemoteAddress '100.64.0.0/10' -Profile Any | Out-Null
}
New-NetFirewallRule -DisplayName $name -Direction Inbound -Action Allow -Protocol TCP -LocalPort $listenPort -Profile $profiles | Out-Null
Write-Host "TCP $listenPort を Private プロファイルで許可しました。Tailscale: $AllowTailscale"
