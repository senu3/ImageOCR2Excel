$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$uv = Get-Command uv -ErrorAction SilentlyContinue

if (-not $uv) {
    Write-Host "uvが見つかりません。uvをインストールしてから再実行してください。"
    exit 1
}

Push-Location $root
try {
    & $uv.Source run image_ocr_excel_app.py
} finally {
    Pop-Location
}
