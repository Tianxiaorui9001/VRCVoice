# VRCVoice 分发版打包脚本
# 用法: powershell -ExecutionPolicy Bypass -File build_dist.ps1 [-Version 0.9.1]
# 产出: dist\VRCVoice_v<版本>.zip (不含个人配置/日志/锁文件)
param(
    [string]$Version
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if ([string]::IsNullOrWhiteSpace($Version)) {
    $Version = (Get-Content -LiteralPath (Join-Path $root "VERSION") -Raw).Trim()
}
if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    throw "版本号必须为 MAJOR.MINOR.PATCH 格式，当前值: '$Version'"
}

$pyinstaller = Join-Path $root ".venv\Scripts\pyinstaller.exe"
$requiredModels = @(
    "encoder-epoch-99-avg-1.int8.onnx",
    "decoder-epoch-99-avg-1.onnx",
    "joiner-epoch-99-avg-1.int8.onnx",
    "tokens.txt",
    "bpe.vocab"
)

Write-Host "==> 1/4 检查构建环境和本地模型"
if (-not (Test-Path -LiteralPath $pyinstaller -PathType Leaf)) {
    throw "缺少 PyInstaller。请先运行: .venv\Scripts\pip install -r requirements-build.txt"
}
foreach ($name in $requiredModels) {
    $modelPath = Join-Path $root "models\$name"
    if (-not (Test-Path -LiteralPath $modelPath -PathType Leaf)) {
        throw "缺少模型文件 $name。请先运行 models\download_model.bat"
    }
}

Write-Host "==> 2/4 PyInstaller 打包"
& $pyinstaller --clean --noconfirm VRCVoice.spec
if ($LASTEXITCODE -ne 0) { throw "打包失败" }

Write-Host "==> 3/4 装配资源、模型和文档"
$dst = Join-Path $root "dist\VRCVoice"
if (-not (Test-Path -LiteralPath $dst -PathType Container)) {
    throw "PyInstaller 未生成预期目录: $dst"
}
$resourceDst = Join-Path $dst "resources"
$modelDst = Join-Path $dst "models"
New-Item -ItemType Directory -Force $resourceDst, $modelDst | Out-Null
Get-ChildItem (Join-Path $root "resources\*.json") -File |
    Copy-Item -Destination $resourceDst -Force
foreach ($name in $requiredModels) {
    Copy-Item -LiteralPath (Join-Path $root "models\$name") -Destination $modelDst -Force
}
foreach ($doc in @("README.md", "使用说明.md", "开发与配置.md", "LICENSE", "VERSION")) {
    Copy-Item -LiteralPath (Join-Path $root $doc) -Destination $dst -Force
}

# 个人数据不带进分发版 (首次运行会自动生成)
foreach ($privateFile in @("config.json", "chatlog.json", "vrcvoice.log", "vrcvoice.lock")) {
    Remove-Item -LiteralPath (Join-Path $dst $privateFile) -Force -ErrorAction SilentlyContinue
}

Write-Host "==> 4/4 压缩分发版"
$zip = Join-Path $root "dist\VRCVoice_v$Version.zip"
Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
Compress-Archive -LiteralPath $dst -DestinationPath $zip -CompressionLevel Optimal
$size = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Host "完成: $zip ($size MB)"
