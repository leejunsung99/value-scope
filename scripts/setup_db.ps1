<#
.SYNOPSIS
  로컬 PostgreSQL에 ValueScope 전용 DB/계정을 만들고 .env의 DATABASE_URL을 설정합니다.

.DESCRIPTION
  - postgres 관리자 비밀번호는 이 창에서만 입력받으며 어디에도 저장하지 않습니다.
  - valuescope 계정 비밀번호는 무작위로 생성되어 .env(Git 제외)에만 기록됩니다.
  - 이미 .env에 DATABASE_URL이 있으면 -Force 없이는 변경하지 않습니다.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\setup_db.ps1
#>
param(
    [string]$PgHost = "localhost",
    [int]$PgPort = 5432,
    [string]$AdminUser = "postgres",
    [string]$AppUser = "valuescope",
    [string]$Database = "valuescope",
    [switch]$Force
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $Root ".env"

function Get-EnvValue([string]$name) {
    if (-not (Test-Path $EnvFile)) { return "" }
    $line = Get-Content $EnvFile -Encoding UTF8 | Where-Object { $_ -match "^$name=" } | Select-Object -First 1
    if ($line) { return $line.Substring($name.Length + 1) } else { return "" }
}

function Set-EnvValue([string]$name, [string]$value) {
    $lines = @(Get-Content $EnvFile -Encoding UTF8)
    $found = $false
    $out = foreach ($l in $lines) {
        if ($l -match "^$name=") { $found = $true; "$name=$value" } else { $l }
    }
    if (-not $found) { $out += "$name=$value" }
    [System.IO.File]::WriteAllLines($EnvFile, $out, (New-Object System.Text.UTF8Encoding($false)))
}

if (-not (Test-Path $EnvFile)) {
    Copy-Item (Join-Path $Root ".env.example") $EnvFile
    Write-Host ".env 파일을 .env.example에서 생성했습니다."
}

if ((Get-EnvValue "DATABASE_URL") -and -not $Force) {
    Write-Host "DATABASE_URL이 이미 설정되어 있습니다. 재설정하려면 -Force 옵션을 사용하세요." -ForegroundColor Yellow
    exit 0
}

$psql = Get-ChildItem "C:\Program Files\PostgreSQL\*\bin\psql.exe" -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending | Select-Object -First 1
if (-not $psql) {
    $cmd = Get-Command psql -ErrorAction SilentlyContinue
    if ($cmd) { $psql = Get-Item $cmd.Source }
}
if (-not $psql) { throw "psql.exe를 찾을 수 없습니다. PostgreSQL을 설치하거나 Docker Compose를 사용하세요." }

$secure = Read-Host "PostgreSQL 관리자($AdminUser) 비밀번호" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
$env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)

try {
    $chars = [char[]]"ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $bytes = New-Object byte[] 28
    $rng.GetBytes($bytes)
    $appPassword = -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })

    $sql = @"
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
"@
    $tmp = New-TemporaryFile
    [System.IO.File]::WriteAllText($tmp, $sql, (New-Object System.Text.UTF8Encoding($false)))
    & $psql.FullName -h $PgHost -p $PgPort -U $AdminUser -d postgres -v ON_ERROR_STOP=1 -q -f $tmp
    $code = $LASTEXITCODE
    Remove-Item $tmp -Force
    if ($code -ne 0) { throw "psql 실행 실패 (비밀번호 또는 접속 정보를 확인하세요)." }

    Set-EnvValue "DATABASE_URL" "postgresql+psycopg://${AppUser}:${appPassword}@${PgHost}:${PgPort}/${Database}"
    Set-EnvValue "TEST_DATABASE_URL" "postgresql+psycopg://${AppUser}:${appPassword}@${PgHost}:${PgPort}/${Database}_test"
    Write-Host "DB '$Database'(및 테스트 DB '${Database}_test')와 계정 '$AppUser' 설정 완료. .env에 DATABASE_URL을 기록했습니다." -ForegroundColor Green
}
finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
}

$py = Join-Path $Root "backend\.venv\Scripts\python.exe"
if (Test-Path $py) {
    Push-Location (Join-Path $Root "backend")
    try { & $py -m alembic upgrade head } finally { Pop-Location }
}
