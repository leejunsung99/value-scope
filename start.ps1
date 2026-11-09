<#
.SYNOPSIS
  ValueScope 로컬 실행 스크립트 (localhost 전용).

.EXAMPLE
  .\start.ps1            # 빌드된 프론트엔드 + API를 http://127.0.0.1:8000 에서 실행
  .\start.ps1 -Dev       # 개발 모드: API(자동 재시작) + Vite 개발서버(http://localhost:5173)
  .\start.ps1 -Rebuild   # 프론트엔드를 다시 빌드한 뒤 실행
  .\start.ps1 -Foreground  # 백그라운드 대신 이 창에서 실행 (Ctrl+C로 종료)
  .\stop.ps1             # 백그라운드 서버 종료
#>
param([switch]$Dev, [switch]$Rebuild, [switch]$NoBrowser, [switch]$Foreground)
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

# Windows 스마트 앱 제어가 평판 없는 파이썬 확장 DLL(psycopg·SQLAlchemy 가속 모듈)을 차단하는 경우가 있어
# 서명된 PostgreSQL libpq.dll과 순수 파이썬 구현을 사용한다 (기능 동일, 속도 차이 미미).
$env:DISABLE_SQLALCHEMY_CEXT_RUNTIME = "1"
$PgBin = Get-ChildItem "C:\Program Files\PostgreSQL\*\bin\libpq.dll" -ErrorAction SilentlyContinue | Sort-Object FullName | Select-Object -Last 1
if ($PgBin) {
    $env:PATH = "$($PgBin.DirectoryName);$env:PATH"
    $env:PSYCOPG_IMPL = "python"
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

# 서버는 하나만: 같은 포트에 이전 ValueScope 서버가 떠 있으면 종료 후 다시 시작한다.
# 다른 프로그램이 포트를 쓰고 있으면 건드리지 않고 안내한다.
function Stop-OldValueScope([int]$port) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($c.OwningProcess)" -ErrorAction SilentlyContinue
        if ($proc -and $proc.CommandLine -match "uvicorn\s+app\.main:app") {
            Write-Host "포트 $port 에서 실행 중인 이전 ValueScope 서버(PID $($proc.ProcessId))를 종료합니다." -ForegroundColor Yellow
            Stop-Process -Id $proc.ProcessId -Force -Confirm:$false
            Start-Sleep -Milliseconds 800
        } elseif ($proc) {
            Write-Host "포트 $port 를 다른 프로그램($($proc.Name), PID $($proc.ProcessId))이 사용 중입니다. .env의 APP_PORT를 바꾸세요." -ForegroundColor Red
            exit 1
        }
    }
}
Stop-OldValueScope ([int]$AppPort)

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
if ($Foreground) {
    Write-Host "ValueScope 실행: $url  (종료: Ctrl+C)" -ForegroundColor Green
    if (-not $NoBrowser) { Start-Job -ScriptBlock { param($u) Start-Sleep 2; Start-Process $u } -ArgumentList $url | Out-Null }
    Push-Location $Backend
    try { & $Py -m uvicorn app.main:app --host $AppHost --port $AppPort } finally { Pop-Location }
    exit
}

# 기본: 창 없이 백그라운드 실행, 출력은 logs\server.log (git 제외)
$LogDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Force $LogDir | Out-Null
$Log = Join-Path $LogDir "server.log"
$ErrLog = Join-Path $LogDir "server.err.log"
$proc = Start-Process -FilePath $Py -ArgumentList "-m", "uvicorn", "app.main:app", "--host", $AppHost, "--port", $AppPort `
    -WorkingDirectory $Backend -WindowStyle Hidden -RedirectStandardOutput $Log -RedirectStandardError $ErrLog -PassThru
$up = $false
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 500
    if ($proc.HasExited) { break }
    try { Invoke-WebRequest -UseBasicParsing "$url/api/health" -TimeoutSec 2 | Out-Null; $up = $true; break } catch {}
}
if (-not $up) {
    Write-Host "서버 시작 실패. 로그 확인: $ErrLog" -ForegroundColor Red
    Get-Content $ErrLog -Tail 20 -ErrorAction SilentlyContinue
    exit 1
}
Write-Host "ValueScope 실행 중 (백그라운드, PID $($proc.Id)): $url" -ForegroundColor Green
Write-Host "로그: $ErrLog   종료: .\stop.ps1"
if (-not $NoBrowser) { Start-Process $url }
