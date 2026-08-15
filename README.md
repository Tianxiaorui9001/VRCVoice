# VRCVoice - VRChat 语音输入助手

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
![Platform: Windows](https://img.shields.io/badge/platform-Windows%2010%2F11-blue.svg)
![Python](https://img.shields.io/badge/python-3.12-yellow.svg)

> 给 VRChat 里不方便说话的人（静音玩家 / 社恐 / 声带状态不佳）准备的「按住说话」工具：
> 按住手柄按键或键盘热键说话，松手即把识别出的文字发进 VRChat 聊天框。

## 功能

- **按住说话**：按住手柄按键（PICO / Index / Quest / WMR 绑定已配好）或键盘热键（默认右 Ctrl）说话，松手自动发送
- **本地识别**：sherpa-onnx 离线流式识别，不联网也能用（约 500MB 模型空间）
- **云端识别**：OpenAI 兼容 ASR 接口（硅基流动等），更准、不占空间，适合低配机器
- **双输出模式**：
  - OSC：发到 VRChat 聊天框气泡显示，录音时显示「正在输入」
  - 键盘：模拟键盘输入真实发进聊天框（可选自动回车）
- **AI 润色 / AI 翻译**（可选，OpenAI 兼容接口）
- **VR 悬浮窗**：识别时头显里显示状态窗（识别中蓝色 / 已发送绿色），位置大小可调
- **桌面悬浮窗**：不戴头显也能看到识别状态
- **全参数设置面板**（Fluent Design），四语言 UI（简中 / 繁中 / 英文 / 日文）
- **开机自启**、托盘菜单、看门狗防卡死

## 使用方法

**方式一：直接下载（推荐）**

1. 到 [Releases](https://github.com/Tianxiaorui9001/VRCVoice/releases) 下载最新的分发版 zip
2. 解压到任意位置（建议桌面或 D 盘），双击 `VRCVoice.exe` 启动
3. 在设置里选择识别后端：**本地**（离线免费）或 **云端**（填 API Key）
4. 启动 SteamVR 进入 VRChat，打开 OSC：菜单 → 设置 → OSC → 启用
5. 按住手柄摇杆（或右 Ctrl）说话，松开即发送

**方式二：源码运行**（面向开发者，见 [开发与配置.md](开发与配置.md)）

## 已知限制

- OSC 模式只是让聊天框显示文本，不会真的发进聊天频道（VRChat 限制）；要真实发送请用键盘模式
- 键盘模式需要聊天输入框已获得焦点

## 相关文档

- 📖 [使用说明.md](使用说明.md) —— 详细使用手册（悬浮窗调校 / 常见问题 / 卸载）
- 🔧 [开发与配置.md](开发与配置.md) —— 源码构建、全部配置项、模型说明、目录结构（面向开发者）

## License

[MIT](LICENSE) © 2026 天小锐
