<#
.SYNOPSIS
  로컬 PostgreSQL에 ValueScope 전용 DB/계정과, 공시알람(dart_monitor) DB 읽기 전용 계정을 설정합니다.

.DESCRIPTION
  1) valuescope 계정 + valuescope / valuescope_test DB 생성  -> .env DATABASE_URL, TEST_DATABASE_URL
  2) valuescope_reader 계정 생성 (dart_monitor DB에 SELECT 권한만, 세션 기본값 읽기 전용)
                                                             -> .env DART_MONITOR_DATABASE_URL
  - 묻는 비밀번호는 PostgreSQL 관리자(postgres) 비밀번호입니다. 입력만 받고 저장하지 않습니다.
  - 새 계정 비밀번호는 무작위로 생성되어 .env(Git 제외)에만 기록됩니다.
  - 이미 설정된 항목은 건너뜁니다. 다시 만들려면 -Force.
  - 공시알람 DB의 데이터·테이블·dart_monitor 계정은 변경하지 않습니다 (권한 부여만).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\setup_db.ps1
#>
param(
    [string]$PgHost = "localhost",
    [int]$PgPort = 5432,
    [string]$AdminUser = "postgres",
    [string]$AppUser = "valuescope",
    [string]$Database = "valuescope",
    [string]$MonitorDatabase = "dart_monitor",
    [string]$MonitorOwner = "dart_monitor",
    [string]$ReaderUser = "valuescope_reader",
    # 관리자 비밀번호를 이미 보관 중인 파일 (POSTGRES_ADMIN_PASSWORD=...). 없으면 입력을 요청합니다.
    [string]$AdminPasswordFile = (Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) "dart-market-monitor\.env.postgres.local"),
    [switch]$SkipMonitor,
    [switch]$Force
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $Root ".env"
$Utf8 = New-Object System.Text.UTF8Encoding($false)

function Get-EnvValue([string]$name) {
    if (-not (Test-Path $EnvFile)) { return "" }
    $line = Get-Content $EnvFile -Encoding UTF8 | Where-Object { $_ -match "^$name=" } | Select-Object -First 1
    if ($line) { return $line.Substring($name.Length + 1).Trim() } else { return "" }
}

function Set-EnvValue([string]$name, [string]$value) {
    $lines = @(Get-Content $EnvFile -Encoding UTF8)
    $found = $false
    $out = foreach ($l in $lines) {
        if ($l -match "^$name=") { $found = $true; "$name=$value" } else { $l }
    }
    if (-not $found) { $out += "$name=$value" }
    [System.IO.File]::WriteAllLines($EnvFile, $out, $Utf8)
}

function New-RandomPassword {
    $chars = [char[]]"ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    $bytes = New-Object byte[] 28
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })
}

function Invoke-Psql([string]$db, [string]$sql) {
    $tmp = New-TemporaryFile
    [System.IO.File]::WriteAllText($tmp, $sql, $Utf8)
    try {
        $out = & $psql.FullName -h $PgHost -p $PgPort -U $AdminUser -d $db -v ON_ERROR_STOP=1 -q -t -A -f $tmp
        if ($LASTEXITCODE -ne 0) { throw "psql 실행 실패 (DB: $db). 관리자 비밀번호 또는 접속 정보를 확인하세요." }
        return $out
    } finally { Remove-Item $tmp -Force }
}

if (-not (Test-Path $EnvFile)) {
    Copy-Item (Join-Path $Root ".env.example") $EnvFile
    Write-Host ".env 파일을 .env.example에서 생성했습니다."
}

$needMain = $Force -or -not (Get-EnvValue "DATABASE_URL")
$needReader = (-not $SkipMonitor) -and ($Force -or -not (Get-EnvValue "DART_MONITOR_DATABASE_URL"))
if (-not $needMain -and -not $needReader) {
    Write-Host "DATABASE_URL과 DART_MONITOR_DATABASE_URL이 이미 설정되어 있습니다. 다시 만들려면 -Force를 사용하세요." -ForegroundColor Yellow
    exit 0
}

$psql = Get-ChildItem "C:\Program Files\PostgreSQL\*\bin\psql.exe" -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending | Select-Object -First 1
if (-not $psql) {
    $cmd = Get-Command psql -ErrorAction SilentlyContinue
    if ($cmd) { $psql = Get-Item $cmd.Source }
}
if (-not $psql) { throw "psql.exe를 찾을 수 없습니다. PostgreSQL을 설치하거나 Docker Compose를 사용하세요." }

$adminPw = ""
if ($AdminPasswordFile -and (Test-Path $AdminPasswordFile)) {
    $line = Get-Content $AdminPasswordFile -Encoding UTF8 | Where-Object { $_ -match "^POSTGRES_ADMIN_PASSWORD=" } | Select-Object -First 1
    if ($line) { $adminPw = $line.Substring("POSTGRES_ADMIN_PASSWORD=".Length).Trim().Trim('"').Trim("'") }
    if ($adminPw) { Write-Host "관리자 비밀번호를 '$AdminPasswordFile'에서 읽었습니다 (저장·출력하지 않음)." }
}
if (-not $adminPw) {
    $secure = Read-Host "PostgreSQL 관리자($AdminUser) 비밀번호" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    $adminPw = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}
$env:PGPASSWORD = $adminPw
$adminPw = $null

try {
    # ---------------------------------------------------------- 1) ValueScope DB
    if ($needMain) {
        $appPassword = New-RandomPassword
        Invoke-Psql "postgres" @"
DO `$`$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$AppUser') THEN
    CREATE ROLE $AppUser LOGIN PASSWORD '$appPassword';
  ELSE
    ALTER ROLE $AppUser WITH LOGIN PASSWORD '$appPassword';
  END IF;
END `$`$;
SELECT 'CREATE DATABASE $Database OWNER $AppUser ENCODING ''UTF8'''
  WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$Database')\gexec
SELECT 'CREATE DATABASE ${Database}_test OWNER $AppUser ENCODING ''UTF8'''
  WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${Database}_test')\gexec
"@ | Out-Null
        Set-EnvValue "DATABASE_URL" "postgresql+psycopg://${AppUser}:${appPassword}@${PgHost}:${PgPort}/${Database}"
        Set-EnvValue "TEST_DATABASE_URL" "postgresql+psycopg://${AppUser}:${appPassword}@${PgHost}:${PgPort}/${Database}_test"
        Write-Host "[1/2] DB '$Database'(+ '${Database}_test')와 계정 '$AppUser' 설정 완료" -ForegroundColor Green
    } else {
        Write-Host "[1/2] DATABASE_URL 이미 설정됨 — 건너뜀"
    }

    # ---------------------------------------------------------- 2) dart_monitor read-only
    if ($needReader) {
        $exists = Invoke-Psql "postgres" "SELECT 1 FROM pg_database WHERE datname = '$MonitorDatabase';"
        if (-not $exists) {
            Write-Host "[2/2] 공시알람 DB '$MonitorDatabase'가 없어 읽기 전용 연동을 건너뜁니다." -ForegroundColor Yellow
        } else {
            $readerPassword = New-RandomPassword
            Invoke-Psql "postgres" @"
DO `$`$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$ReaderUser') THEN
    CREATE ROLE $ReaderUser LOGIN PASSWORD '$readerPassword' NOSUPERUSER NOCREATEDB NOCREATEROLE;
  ELSE
    ALTER ROLE $ReaderUser WITH LOGIN PASSWORD '$readerPassword' NOSUPERUSER NOCREATEDB NOCREATEROLE;
  END IF;
END `$`$;
ALTER ROLE $ReaderUser SET default_transaction_read_only = on;
ALTER ROLE $ReaderUser CONNECTION LIMIT 5;
GRANT CONNECT ON DATABASE $MonitorDatabase TO $ReaderUser;
"@ | Out-Null
            # Grants inside the monitor DB: read only. Default privileges cover tables the
            # monitor creates later (it runs CREATE TABLE IF NOT EXISTS at startup).
            Invoke-Psql $MonitorDatabase @"
REVOKE CREATE ON SCHEMA public FROM $ReaderUser;
GRANT USAGE ON SCHEMA public TO $ReaderUser;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO $ReaderUser;
ALTER DEFAULT PRIVILEGES FOR ROLE $MonitorOwner IN SCHEMA public GRANT SELECT ON TABLES TO $ReaderUser;
"@ | Out-Null
            Set-EnvValue "DART_MONITOR_DATABASE_URL" "postgresql+psycopg://${ReaderUser}:${readerPassword}@${PgHost}:${PgPort}/${MonitorDatabase}"
            Write-Host "[2/2] 공시알람 DB 읽기 전용 계정 '$ReaderUser' 설정 완료 (SELECT 권한만)" -ForegroundColor Green
        }
    } else {
        Write-Host "[2/2] DART_MONITOR_DATABASE_URL 이미 설정됨 — 건너뜀"
    }
}
finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
}

$py = Join-Path $Root "backend\.venv\Scripts\python.exe"
if ($needMain -and (Test-Path $py)) {
    Write-Host "ValueScope DB 마이그레이션 적용 중..."
    Push-Location (Join-Path $Root "backend")
    try { & $py -m alembic upgrade head } finally { Pop-Location }
}
Write-Host ".env 설정이 완료되었습니다." -ForegroundColor Green
