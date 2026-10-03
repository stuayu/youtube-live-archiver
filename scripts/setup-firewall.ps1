param([switch]$AllowTailscale)
$ErrorActionPreference = 'Stop'
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw '管理者 PowerShell で実行してください。' }
$port = 8000
$envFile = Join-Path $PSScriptRoot '..\.env'
if (Test-Path $envFile) {
    $line = Get-Content $envFile | Where-Object { $_ -match '^APP_PORT=' } | Select-Object -Last 1
    if ($line -and $line -match '^APP_PORT=(\d+)$') { $port = [int]$Matches[1] }
}
$name = 'YouTube Live Archiver TCP ' + $port
Remove-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue
$profiles = 'Private'
if ($AllowTailscale) {
    New-NetFirewallRule -DisplayName ($name + ' Tailscale') -Direction Inbound -Action Allow -Protocol TCP -LocalPort $port -RemoteAddress '100.64.0.0/10' -Profile Any | Out-Null
}
New-NetFirewallRule -DisplayName $name -Direction Inbound -Action Allow -Protocol TCP -LocalPort $port -Profile $profiles | Out-Null
Write-Host "TCP $port を Private プロファイルで許可しました。Tailscale: $AllowTailscale"
