# FlipaBit (Ozdilek Toptan) uygulamasini ve ona ait Cloudflare Tunnel'ini
# oturum acilisinda, pencere acmadan, hata/yeniden baslatma sonrasi otomatik
# ayaga kaldiracak sekilde Gorev Zamanlayici'ya kaydeder.
#
# Iki ayri gorev kaydedilir:
#   FlipaBit-Sunucu  -> app.py (Flask, port 5300)
#   FlipaBit-Tunnel  -> cloudflared tunnel run (flipabit-toptan, toptan.ozgunaydin.com.tr)
# Bu tunel, YBox/UrunEkleme/SoruCevap/SiparisPaneli'nin kullandigi "panel"
# tunelinden (ruyaev.online) tamamen ayridir -- kendi cloudflared surecidir.
#
# Kullanim: PowerShell'i yonetici olarak acmaya GEREK YOK, bunlar
# "per-user logon" gorevleri.
#   powershell -ExecutionPolicy Bypass -File gorev_zamanlayici_kur.ps1

$ErrorActionPreference = "Stop"

$ProjectRoot    = "C:\Users\DESKTOP-3KS3D8E\Desktop\Python Test\Claude-Projects\FlipaBit"
$PythonwExe     = Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"
$AppScript      = Join-Path $ProjectRoot "app.py"
$TunnelConfig   = Join-Path $ProjectRoot "credentials\cloudflared_tunnel_config.yml"
$CloudflaredExe = "C:\Program Files (x86)\cloudflared\cloudflared.exe"

$CommonSettings = New-ScheduledTaskSettingsSet `
    -Hidden `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

$LogonTrigger = New-ScheduledTaskTrigger -AtLogOn

# --- Gorev 1: Flask sunucusu ---
$ServerAction = New-ScheduledTaskAction -Execute $PythonwExe -Argument "`"$AppScript`"" -WorkingDirectory $ProjectRoot
Register-ScheduledTask -TaskName "FlipaBit-Sunucu" -Action $ServerAction -Trigger $LogonTrigger -Settings $CommonSettings `
    -Description "FlipaBit / Ozdilek Toptan Flask sunucusu (app.py, port 5300) -- oturum acilisinda otomatik baslar" -Force

# --- Gorev 2: Cloudflare Tunnel ---
# ONEMLI (2026-09-28'de bulundu): cloudflared.exe'yi wscript.exe + gizli .vbs
# sarmalayicisiyla "fırlat ve unut" seklinde baslatmak, sureci Gorev
# Zamanlayici'nin izlemesinden tamamen cikariyordu -- wscript ani donunce
# gorev "basariyla bitti" sayiliyor, cloudflared cokerse/aglantisi
# duserse 999 kere yeniden deneme ayari HIC devreye girmiyordu (4 gun
# boyunca tunel dustu, kimse fark etmedi). Ilk cozum denemesi (S4U logon ile
# dogrudan cloudflared.exe) bu makinedeki etki alani hesabinda
# "Register-ScheduledTask: Parametre hatali (UserId)" ile reddedildi
# (2026-09-29) - yani o kayit hic olusmamisti, eski .vbs gorevi (dosyasi
# silinmis halde!) duruyordu. Gecerli cozum: scripts\tunnel_baslat.ps1 --
# gizli bir powershell cloudflared'i penceresiz baslatip -Wait ile bekler;
# Gorev Zamanlayici powershell'i izler, cloudflared cokunce o da biter ->
# yeniden baslatma devreye girer. Oturum acik kullanici hesabiyla (Sunucu
# goreviyle ayni) calisir, sifre/S4U gerekmez.
$PowershellExe = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$TunnelScript  = Join-Path $PSScriptRoot "tunnel_baslat.ps1"
$TunnelAction  = New-ScheduledTaskAction -Execute $PowershellExe `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$TunnelScript`"" -WorkingDirectory $ProjectRoot
Register-ScheduledTask -TaskName "FlipaBit-Tunnel" -Action $TunnelAction -Trigger $LogonTrigger -Settings $CommonSettings `
    -Description "FlipaBit / toptan.ozgunaydin.com.tr icin ayri Cloudflare Tunnel (flipabit-toptan) -- oturum acilisinda otomatik baslar, penceresiz, cokerse Gorev Zamanlayici tarafindan izlenip yeniden baslatilir (scripts\tunnel_baslat.ps1)" -Force

# --- Gorev 3: Wix senkronu (Sheets -> www.ozgunaydin.net), GUNDE 1 KEZ 06:00 ---
# core/wix_sync.py --uygula: urun fiyat/ad/stok/gorsel/aciklama + kategori
# koleksiyonlari + ceviriler + kategori agaci JSON (Velo menu/anasayfa buradan
# beslenir). Kullanici karari 2026-09-29: 15 dk degil, gunde bir. Bilgisayar o
# saatte kapaliysa -StartWhenAvailable sayesinde acilir acilmaz calisir.
# Log: logs/wix_sync.log (her calismada eklenir). Ayrinti: docs/WIX_KURULUM.md
$WixScript   = Join-Path $PSScriptRoot "wix_senkron_calistir.ps1"
$WixAction   = New-ScheduledTaskAction -Execute $PowershellExe `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$WixScript`"" -WorkingDirectory $ProjectRoot
$WixTrigger  = New-ScheduledTaskTrigger -Daily -At 06:00
$WixSettings = New-ScheduledTaskSettingsSet -Hidden -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "FlipaBit-WixSenkron" -Action $WixAction -Trigger $WixTrigger -Settings $WixSettings `
    -Description "Google Sheets katalogunu www.ozgunaydin.net (Wix Stores) ile her gun 06:00'da senkronlar: urunler, kategoriler, ceviriler, kategori agaci (scripts\wix_senkron_calistir.ps1)" -Force

Write-Host "Gorevler kaydedildi: FlipaBit-Sunucu, FlipaBit-Tunnel, FlipaBit-WixSenkron"
Write-Host "Kontrol icin: Get-ScheduledTask -TaskName 'FlipaBit-*'"
