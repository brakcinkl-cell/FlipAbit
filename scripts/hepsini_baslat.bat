@echo off
cd /d "%~dp0.."
echo FlipaBit sunucusu ve Cloudflare Tunnel'i ayri pencerelerde baslatiliyor...
start "FlipaBit - Sunucu" cmd /k ""%~dp0..\.venv\Scripts\python.exe" app.py"
start "FlipaBit - Tunnel" cmd /k ""cloudflared" tunnel --config "%~dp0..\credentials\cloudflared_tunnel_config.yml" run"
echo Iki pencere acildi. Site: https://toptan.ozgunaydin.com.tr (yerel: http://localhost:5300)
