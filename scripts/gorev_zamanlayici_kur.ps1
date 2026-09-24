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

$ProjectRoot   = "C:\Users\DESKTOP-3KS3D8E\Desktop\Python Test\Claude-Projects\FlipaBit"
$PythonwExe    = Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"
$AppScript     = Join-Path $ProjectRoot "app.py"
$TunnelConfig  = Join-Path $ProjectRoot "credentials\cloudflared_tunnel_config.yml"
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

$Trigger = New-ScheduledTaskTrigger -AtLogOn

# --- Gorev 1: Flask sunucusu ---
$ServerAction = New-ScheduledTaskAction -Execute $PythonwExe -Argument "`"$AppScript`"" -WorkingDirectory $ProjectRoot
Register-ScheduledTask -TaskName "FlipaBit-Sunucu" -Action $ServerAction -Trigger $Trigger -Settings $CommonSettings `
    -Description "FlipaBit / Ozdilek Toptan Flask sunucusu (app.py, port 5300) -- oturum acilisinda otomatik baslar" -Force

# --- Gorev 2: Cloudflare Tunnel ---
$TunnelAction = New-ScheduledTaskAction -Execute $CloudflaredExe -Argument "tunnel --config `"$TunnelConfig`" run" -WorkingDirectory $ProjectRoot
Register-ScheduledTask -TaskName "FlipaBit-Tunnel" -Action $TunnelAction -Trigger $Trigger -Settings $CommonSettings `
    -Description "FlipaBit / toptan.ozgunaydin.com.tr icin ayri Cloudflare Tunnel (flipabit-toptan) -- oturum acilisinda otomatik baslar" -Force

Write-Host "Gorevler kaydedildi: FlipaBit-Sunucu, FlipaBit-Tunnel"
Write-Host "Kontrol icin: Get-ScheduledTask -TaskName 'FlipaBit-*'"
