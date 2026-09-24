' cloudflared bir konsol uygulamasi oldugu icin Gorev Zamanlayici'dan
' dogrudan calistirilirsa oturum acilisinda goze batan siyah bir pencere
' aciyor. Bu VBScript, WScript.Shell.Run'in 3. parametresi (0 = gizli
' pencere) ile onu tamamen arka planda, penceresiz baslatir.
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run """C:\Program Files (x86)\cloudflared\cloudflared.exe"" tunnel --config ""C:\Users\DESKTOP-3KS3D8E\Desktop\Python Test\Claude-Projects\FlipaBit\credentials\cloudflared_tunnel_config.yml"" run", 0, False
