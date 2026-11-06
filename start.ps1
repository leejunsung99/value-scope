<#
.SYNOPSIS
  ValueScope 로컬 실행 스크립트 (localhost 전용).

.EXAMPLE
  .\start.ps1            # 빌드된 프론트엔드 + API를 http://127.0.0.1:8000 에서 실행
  .\start.ps1 -Dev       # 개발 모드: API(자동 재시작) + Vite 개발서버(http://localhost:5173)
  .\start.ps1 -Rebuild   # 프론트엔드를 다시 빌드한 뒤 실행
#>
param([switch]$Dev, [switch]$Rebuild, [switch]$NoBrowser)
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$Py = Join-Path $Backend ".venv\Scripts\python.exe"
$EnvFile = Join-Path $Root ".env"

function Get-EnvValue([string]$name, [string]$default) {
    if (Test-Path $EnvFile) {
        $line = Get-Content $EnvFile -Encoding UTF8 | Where-Object { $_ -match "^$name=" } | Select-Object -First 1
        if ($line) { $v = $line.Substring($name.Length + 1); if ($v) { return $v } }
    }
    return $default
}

if (-not (Test-Path $EnvFile)) {
    Copy-Item (Join-Path $Root ".env.example") $EnvFile
    Write-Host ".env를 생성했습니다. API Key를 입력하고 scripts\setup_db.ps1로 DB를 설정하세요." -ForegroundColor Yellow
}
if (-not (Get-EnvValue "DATABASE_URL" "")) {
    Write-Host "DATABASE_URL이 비어 있습니다. 먼저 실행: powershell -ExecutionPolicy Bypass -File scripts\setup_db.ps1" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $Py)) {
    Write-Host "Python 가상환경 생성 중..."
    python -m venv (Join-Path $Backend ".venv")
    & $Py -m pip install -q --upgrade pip
    & $Py -m pip install -q -r (Join-Path $Backend "requirements.txt")
}
if (-not (Test-Path (Join-Path $Frontend "node_modules"))) {
    Write-Host "프론트엔드 의존성 설치 중..."
    Push-Location $Frontend; try { npm install } finally { Pop-Location }
}

Write-Host "DB 마이그레이션 적용 중..."
Push-Location $Backend
try { & $Py -m alembic upgrade head; if ($LASTEXITCODE -ne 0) { throw "alembic upgrade 실패" } } finally { Pop-Location }

$AppHost = Get-EnvValue "APP_HOST" "127.0.0.1"
if ($AppHost -notin @("127.0.0.1", "localhost")) {
    Write-Host "보안을 위해 APP_HOST는 127.0.0.1/localhost만 허용합니다. 127.0.0.1로 실행합니다." -ForegroundColor Yellow
    $AppHost = "127.0.0.1"
}
$AppPort = Get-EnvValue "APP_PORT" "8000"

if ($Dev) {
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$Frontend'; npm run dev"
    if (-not $NoBrowser) { Start-Process "http://localhost:5173" }
    Push-Location $Backend
    try { & $Py -m uvicorn app.main:app --host $AppHost --port $AppPort --reload } finally { Pop-Location }
    exit
}

if ($Rebuild -or -not (Test-Path (Join-Path $Frontend "dist\index.html"))) {
    Write-Host "프론트엔드 빌드 중..."
    Push-Location $Frontend; try { npm run build; if ($LASTEXITCODE -ne 0) { throw "frontend build 실패" } } finally { Pop-Location }
}

$url = "http://${AppHost}:${AppPort}"
Write-Host "ValueScope 실행: $url  (종료: Ctrl+C)" -ForegroundColor Green
if (-not $NoBrowser) { Start-Job -ScriptBlock { param($u) Start-Sleep 2; Start-Process $u } -ArgumentList $url | Out-Null }
Push-Location $Backend
try { & $Py -m uvicorn app.main:app --host $AppHost --port $AppPort } finally { Pop-Location }
