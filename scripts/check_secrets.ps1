<#
.SYNOPSIS
  커밋/푸시 전 민감정보 검사. 문제가 있으면 exit 1.
  1) 금지 파일(.env, DB 덤프, data/ 등)이 Git에 추적·스테이징되었는지
  2) Gitleaks로 스테이징 내용 및 전체 이력 스캔 (설치된 경우)
#>
param([switch]$History)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root
$fail = $false
try {
    $tracked = @(git ls-files) + @(git diff --cached --name-only)
    $forbidden = $tracked | Where-Object {
        ($_ -match '(^|/)\.env($|\.)' -and $_ -notmatch '\.env\.example$') -or
        $_ -match '\.(dump|backup|sqlite3?|db|pem|key|pfx|p12)$' -or
        $_ -match '^(data|storage|raw|reports|exports|logs)/' -or
        $_ -match '(^|/)(\.venv|node_modules)/'
    } | Sort-Object -Unique
    if ($forbidden) {
        Write-Host "[차단] Git에 올라가면 안 되는 파일:" -ForegroundColor Red
        $forbidden | ForEach-Object { Write-Host "  $_" }
        $fail = $true
    }

    $gl = Get-Command gitleaks -ErrorAction SilentlyContinue
    $glPath = if ($gl) { $gl.Source } else {
        Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Gitleaks*\gitleaks.exe" -ErrorAction SilentlyContinue |
            Select-Object -First 1 -ExpandProperty FullName
    }
    if ($glPath) {
        & $glPath git --staged --no-banner --redact --config .gitleaks.toml .
        if ($LASTEXITCODE -ne 0) { $fail = $true }
        if ($History) {
            & $glPath git --no-banner --redact --config .gitleaks.toml .
            if ($LASTEXITCODE -ne 0) { $fail = $true }
        }
    } else {
        Write-Host "[경고] gitleaks 미설치 — 설치: winget install Gitleaks.Gitleaks" -ForegroundColor Yellow
    }
}
finally { Pop-Location }
if ($fail) { Write-Host "민감정보 검사 실패 — 커밋/푸시를 중단하세요." -ForegroundColor Red; exit 1 }
Write-Host "민감정보 검사 통과" -ForegroundColor Green
