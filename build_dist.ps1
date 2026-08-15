# VRCVoice 分发版打包脚本
# 用法: powershell -ExecutionPolicy Bypass -File build_dist.ps1
# 产出: dist\VRCVoice_分发版_yyyyMMdd.zip (不含个人配置/日志/锁文件)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host "==> 1/4 停止运行中的 VRCVoice"
Get-Process -Name VRCVoice -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2

Write-Host "==> 2/4 PyInstaller 打包"
& ".venv\Scripts\pyinstaller.exe" --noconfirm VRCVoice.spec
if ($LASTEXITCODE -ne 0) { throw "打包失败" }

Write-Host "==> 3/4 同步资源(models/resources) 并清理个人数据"
$dst = Join-Path $root "dist\VRCVoice"
New-Item -ItemType Directory -Force (Join-Path $dst "resources") | Out-Null
Get-ChildItem (Join-Path $root "resources\*.json") | Copy-Item -Destination (Join-Path $dst "resources\") -Force
Copy-Item (Join-Path $root "models\*") (Join-Path $dst "models\") -Recurse -Force
Copy-Item (Join-Path $root "使用说明.txt") $dst -Force
# 同步 APPDATA 数据目录(frozen 版实际从这里加载 manifest/bindings)
$appdataRes = Join-Path $env:APPDATA "VRCVoice\resources"
New-Item -ItemType Directory -Force $appdataRes | Out-Null
Get-ChildItem (Join-Path $root "resources\*.json") | Copy-Item -Destination $appdataRes -Force
# 个人数据不带进分发版 (首次运行会自动生成)
Remove-Item (Join-Path $dst "config.json") -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $dst "vrcvoice.log") -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $dst "vrcvoice.lock") -Force -ErrorAction SilentlyContinue

Write-Host "==> 4/4 压缩分发版"
$stamp = Get-Date -Format "yyyyMMdd"
$zip = Join-Path $root "dist\VRCVoice_分发版_$stamp.zip"
Remove-Item $zip -Force -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $dst "*") -DestinationPath $zip -CompressionLevel Optimal
$size = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Host "完成: $zip ($size MB)"
