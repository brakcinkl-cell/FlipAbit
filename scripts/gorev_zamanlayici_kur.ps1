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
# boyunca tunel dustu, kimse fark etmedi). Cozum: cloudflared.exe'yi
# DOGRUDAN, "oturum acik olsun olmasin calistir" (S4U logon, sifre
# saklanmaz) prensibiyle calistirmak -- bu hem penceresiz kalir (S4U
# etkilesimsiz oturumda calisir) HEM DE Gorev Zamanlayici artik gercek
# cloudflared surecini izleyip cokerse yeniden baslatabilir.
$TunnelAction = New-ScheduledTaskAction -Execute $CloudflaredExe -Argument "tunnel --config `"$TunnelConfig`" run" -WorkingDirectory $ProjectRoot
$TunnelPrincipal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Limited
$TunnelTriggers = @($LogonTrigger, (New-ScheduledTaskTrigger -AtStartup))
Register-ScheduledTask -TaskName "FlipaBit-Tunnel" -Action $TunnelAction -Trigger $TunnelTriggers -Principal $TunnelPrincipal -Settings $CommonSettings `
    -Description "FlipaBit / toptan.ozgunaydin.com.tr icin ayri Cloudflare Tunnel (flipabit-toptan) -- oturum acilisinda/sistem baslangicinda otomatik baslar, penceresiz, cokerse Gorev Zamanlayici tarafindan gercekten izlenip yeniden baslatilir" -Force

# --- Gorev 3: Wix senkronu (Sheets -> www.ozgunaydin.net), 15 dakikada bir ---
# core/wix_sync.py --uygula: urun fiyat/ad/stok/gorsel/aciklama + kategori
# koleksiyonlari + ceviriler + kategori agaci JSON (Velo menu/anasayfa buradan
# beslenir). Log: logs/wix_sync.log (her calismada eklenir). Ayrinti: docs/WIX_KURULUM.md
$PythonExe   = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$WixSyncCmd  = "/c `"set PYTHONIOENCODING=utf-8&& `"$PythonExe`" -m core.wix_sync --uygula >> `"$ProjectRoot\logs\wix_sync.log`" 2>&1`""
$WixAction   = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\cmd.exe" -Argument $WixSyncCmd -WorkingDirectory $ProjectRoot
$WixTrigger  = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Days 3650)
$WixSettings = New-ScheduledTaskSettingsSet -Hidden -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew
$WixPrincipal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Limited
Register-ScheduledTask -TaskName "FlipaBit-WixSenkron" -Action $WixAction -Trigger $WixTrigger -Principal $WixPrincipal -Settings $WixSettings `
    -Description "Google Sheets katalogunu www.ozgunaydin.net (Wix Stores) ile 15 dakikada bir senkronlar: urunler, kategoriler, ceviriler, kategori agaci" -Force

Write-Host "Gorevler kaydedildi: FlipaBit-Sunucu, FlipaBit-Tunnel, FlipaBit-WixSenkron"
Write-Host "Kontrol icin: Get-ScheduledTask -TaskName 'FlipaBit-*'"
