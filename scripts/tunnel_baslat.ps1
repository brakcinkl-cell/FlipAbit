# FlipaBit-Tunnel gorevinin calistirdigi sarmalayici.
# cloudflared.exe'yi PENCERESIZ baslatir ve bitmesini BEKLER: Gorev
# Zamanlayici bu powershell surecini izler; cloudflared cokerse/kapanirsa
# powershell de ayni anda biter -> gorevin "hata sonrasi yeniden baslat"
# ayari devreye girer. (Eski wscript/.vbs "firlat ve unut" yontemi sureci
# izlenmez birakiyordu; S4U logon ise bu makinedeki etki alani hesabinda
# "Parametre hatali (UserId)" ile reddedildi - 2026-09-29.)
$ProjectRoot    = Split-Path -Parent $PSScriptRoot
$CloudflaredExe = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
$TunnelConfig   = Join-Path $ProjectRoot "credentials\cloudflared_tunnel_config.yml"

$p = Start-Process -FilePath $CloudflaredExe `
    -ArgumentList @("tunnel", "--config", "`"$TunnelConfig`"", "run") `
    -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru -Wait
exit $p.ExitCode
