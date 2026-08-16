# VRCVoice 分发版打包脚本
# 用法: powershell -ExecutionPolicy Bypass -File build_dist.ps1 [-Version 0.9.2]
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

Write-Host "==> 2.5/4 瘦身: 清理未使用的 Qt 模块/插件/翻译(体积优化)"
$dst = Join-Path $root "dist\VRCVoice"
$qtDir = Join-Path $dst "_internal\PySide6"
$qtTrim = @(
    "opengl32sw.dll",          # 软件 OpenGL 渲染(项目不用 OpenGL)
    "Qt6Quick.dll",            # QML/Quick 全家桶(项目用 Widgets)
    "Qt6Qml.dll",
    "Qt6QmlCore.dll",
    "Qt6QmlModels.dll",
    "Qt6QmlLocalStorage.dll",
    "Qt6QuickControls2.dll",
    "Qt6QuickControls2Impl.dll",
    "Qt6QuickTemplates2.dll",
    "Qt6QuickShapes.dll",
    "Qt6QuickWidgets.dll",
    "Qt6Pdf.dll",              # PDF
    "Qt6OpenGL.dll",           # OpenGL
    "Qt6OpenGLWidgets.dll",
    "Qt6Network.dll",          # 网络(Qt 侧无用; 项目走 requests)
    "Qt6Multimedia.dll",       # 多媒体/FFmpeg
    "Qt6MultimediaWidgets.dll",
    "Qt6WebSockets.dll",
    "Qt6WebChannel.dll",
    "Qt6PrintSupport.dll",
    "Qt6Sql.dll",
    "Qt6DBus.dll",
    "Qt6Test.dll",
    "Qt6Charts.dll",
    "Qt6DataVisualization.dll",
    "Qt63DCore.dll",
    "Qt63DRender.dll",
    "Qt6Positioning.dll",
    "Qt6Sensors.dll",
    "Qt6SerialPort.dll",
    "Qt6StateMachine.dll",
    "Qt6TextToSpeech.dll",
    "Qt6Bluetooth.dll",
    "Qt6Nfc.dll",
    "Qt6Help.dll",
    "Qt6UiTools.dll",
    "Qt6Designer.dll",
    "Qt6WebEngineCore.dll",
    "Qt6WebEngineWidgets.dll",
    "Qt6SpatialAudio.dll"
)
foreach ($f in $qtTrim) {
    Remove-Item -LiteralPath (Join-Path $qtDir $f) -Force -ErrorAction SilentlyContinue
}
# Qt 自带翻译文件(应用自研 i18n, 不需要)
Remove-Item -LiteralPath (Join-Path $qtDir "translations") -Recurse -Force -ErrorAction SilentlyContinue
# 无用平台/输入插件
foreach ($p in @("plugins\platforms\qdirect2d.dll", "plugins\generic\qtuiotouchplugin.dll", "plugins\platforminputcontexts\qtvirtualkeyboardplugin.dll")) {
    Remove-Item -LiteralPath (Join-Path $qtDir $p) -Force -ErrorAction SilentlyContinue
}

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

# 内置热词预设(只读资源; 首次运行自动复制到数据目录, 用户可删可改)
$presetSrc = Join-Path $root "presets\VRChat热词预设.json"
if (Test-Path -LiteralPath $presetSrc -PathType Leaf) {
    $presetDst = Join-Path $dst "presets"
    New-Item -ItemType Directory -Force $presetDst | Out-Null
    Copy-Item -LiteralPath $presetSrc -Destination $presetDst -Force
}
foreach ($name in $requiredModels) {
    Copy-Item -LiteralPath (Join-Path $root "models\$name") -Destination $modelDst -Force
}
foreach ($doc in @("README.md", "使用说明.md", "开发与配置.md", "LICENSE", "VERSION")) {
    Copy-Item -LiteralPath (Join-Path $root $doc) -Destination $dst -Force
}
# 语言文件(i18n 从程序目录旁 Language/ 加载, 必须随包分发)
$langSrc = Join-Path $root "Language"
if (Test-Path -LiteralPath $langSrc -PathType Container) {
    $langDst = Join-Path $dst "Language"
    New-Item -ItemType Directory -Force $langDst | Out-Null
    Copy-Item (Join-Path $langSrc "*.json") -Destination $langDst -Force
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
