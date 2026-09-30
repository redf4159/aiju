# 同步 web 到服务器（不含密码；需已配置 SSH 密钥或手动输入密码）
# 用法: .\sync_to_server.ps1
# 或（仓库根目录，含自动重启）: python ..\_sync_bingjing_web.py
# 制作台以线上 http://175.155.64.171:61951 为准，改代码后请同步。
$ErrorActionPreference = "Stop"
$HostAddr = "175.155.64.171"
$Port = 24195
$User = "linux"
$LocalWeb = "$PSScriptRoot\..\web"
$Remote = "/home/linux/bingjing/web"

Write-Host "Sync $LocalWeb -> ${User}@${HostAddr}:$Remote"
scp -P $Port -r "$LocalWeb\app" "$LocalWeb\data" "$LocalWeb\requirements.txt" "${User}@${HostAddr}:$Remote/"
# 请在服务器 ~/.config/systemd/user/bingjing-web.service 配置 WEB_USER/WEB_PASSWORD，优先 systemctl --user restart bingjing-web
ssh -p $Port "${User}@${HostAddr}" "systemctl --user restart bingjing-web.service 2>/dev/null || true"
Write-Host "Done. Open http://${HostAddr}:61951"
