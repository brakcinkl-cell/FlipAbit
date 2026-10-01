# FlipaBit-WixSenkron gorevinin calistirdigi sarmalayici (gunde 1 kez 06:00).
# Sheets -> Wix Stores senkronunu penceresiz calistirir, ciktiyi
# logs\wix_sync.log dosyasina ekler. Elle calistirmak icin de kullanilabilir:
#   powershell -ExecutionPolicy Bypass -File scripts\wix_senkron_calistir.ps1
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonExe   = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$LogDir      = Join-Path $ProjectRoot "logs"
$Log         = Join-Path $LogDir "wix_sync.log"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
Set-Location $ProjectRoot

"==== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') senkron basladi" | Out-File -Append -Encoding utf8 $Log
$env:PYTHONIOENCODING = "utf-8"
# Once ceviri bakimi (kullanici istegi 2026-10-01: yeni urun/kategori gelince
# EN/RU cevirisi de otomatik gelsin) - yeni barkod/kategori icin Sheets'e
# GOOGLETRANSLATE formulu yazar. Hata verse bile Wix senkronu yine calisir.
& "$env:SystemRoot\System32\cmd.exe" /c "`"$PythonExe`" -m core.ceviri_bakim >> `"$Log`" 2>&1"
"==== ceviri bakimi bitti (cikis kodu $LASTEXITCODE)" | Out-File -Append -Encoding utf8 $Log
& "$env:SystemRoot\System32\cmd.exe" /c "`"$PythonExe`" -m core.wix_sync --uygula >> `"$Log`" 2>&1"
"==== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') senkron bitti (cikis kodu $LASTEXITCODE)" | Out-File -Append -Encoding utf8 $Log
exit $LASTEXITCODE
