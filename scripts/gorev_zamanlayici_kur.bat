@echo off
chcp 65001 >nul
title FlipaBit - Gorev Zamanlayici Kaydi
cd /d "%~dp0.."

echo FlipaBit sunucusunu ve Cloudflare Tunnel'ini oturum acilisinda
echo otomatik baslatacak Gorev Zamanlayici kayitlari olusturuluyor...
echo.
powershell -ExecutionPolicy Bypass -File "%~dp0gorev_zamanlayici_kur.ps1"

echo.
pause
