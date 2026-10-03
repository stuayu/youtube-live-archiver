$ErrorActionPreference = 'Stop'
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw '管理者 PowerShell で実行してください。' }
$name = 'YouTubeLiveArchiver'
if (Get-Service $name -ErrorAction SilentlyContinue) {
    Stop-Service $name -Force -ErrorAction SilentlyContinue
    $nssm = Get-Command nssm.exe -ErrorAction SilentlyContinue
    if ($nssm) { & $nssm.Source remove $name confirm } else { sc.exe delete $name | Out-Null }
}
Unregister-ScheduledTask -TaskName 'YouTube Live Archiver' -Confirm:$false -ErrorAction SilentlyContinue
Write-Host 'YouTube Live Archiver のサービス/タスクを解除しました。'
