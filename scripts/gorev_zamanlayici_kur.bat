@echo off
chcp 65001 >nul
title FlipaBit - Gorev Zamanlayici Kaydi

:: Yonetici degilse, UAC ile kendini yukselterek yeniden baslat
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Yonetici izni gerekiyor, UAC penceresi acilacak...
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

cd /d "%~dp0.."

echo FlipaBit sunucusunu ve Cloudflare Tunnel'ini oturum acilisinda
echo otomatik baslatacak Gorev Zamanlayici kayitlari olusturuluyor...
echo.
powershell -ExecutionPolicy Bypass -File "%~dp0gorev_zamanlayici_kur.ps1"

echo.
pause
