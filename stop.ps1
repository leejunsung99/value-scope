<#
.SYNOPSIS
  백그라운드로 실행 중인 ValueScope 서버를 종료한다 (.env의 APP_PORT 기준, ValueScope uvicorn 프로세스만).
#>
$Root = $PSScriptRoot
$EnvFile = Join-Path $Root ".env"
$port = 8000
if (Test-Path $EnvFile) {
    $line = Get-Content $EnvFile -Encoding UTF8 | Where-Object { $_ -match "^APP_PORT=" } | Select-Object -First 1
    if ($line -and $line.Substring(9)) { $port = [int]$line.Substring(9) }
}
$stopped = $false
foreach ($c in (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)) {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($c.OwningProcess)" -ErrorAction SilentlyContinue
    if ($proc -and $proc.CommandLine -match "uvicorn\s+app\.main:app") {
        Stop-Process -Id $proc.ProcessId -Force -Confirm:$false
        Write-Host "ValueScope 서버(PID $($proc.ProcessId), 포트 $port)를 종료했습니다." -ForegroundColor Yellow
        $stopped = $true
    }
}
if (-not $stopped) { Write-Host "포트 $port 에서 실행 중인 ValueScope 서버가 없습니다." }