<p align="center">
  <img src="assets/logo_big.png" width="512" alt="VRCVoice">
</p>


# VRCVoice - VRChat 语音输入助手



[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
![Platform: Windows](https://img.shields.io/badge/platform-Windows%2010%2F11-blue.svg)
![Python](https://img.shields.io/badge/python-3.12-yellow.svg)

> 给 VRChat 里不方便说话的人（静音玩家 / 社恐 / 声带状态不佳）准备的「按住说话」工具：
> 按住手柄按键或键盘热键说话，松手即把识别出的文字发进 VRChat 聊天框。

- 由于身边有很多无言势好友，每回看他们打字很慢或是不方便说话都在想这个问题，今天我和我的DeepSeek V4F讨论了下，于是开干：
- 由于是Agent写出的代码，Bug可能会很多，我一直在努力完善的！
- 欢迎提Issues或者大佬Pull Requests，觉得项目有用麻烦给个stars谢谢啦~
## 功能

- **按住说话**：按住手柄按键（默认左摇杆）或键盘热键（默认右 Ctrl）说话，松手自动发送
- **本地识别**：sherpa-onnx 离线流式识别，不联网也能用（约 200MB 模型空间）
- **识别热词表**：专有名词、游戏黑话、好友昵称识别不准？在设置里给模型加「热词表」——可视化编辑词条和权重，支持导入预设、预设分组，保存即生效，识别立刻"听得懂人话"
- **云端识别**：OpenAI 兼容 ASR 接口，支持接入多家api，更准、不占空间，适合低配机器
- **VR 悬浮窗**：VR 无言势玩家在交流上一直是个问题……这次我们把SteamVR 叠加界面作为重点开发，让VR无言势也能有比较流畅的沟通啦~！
- **双输出模式**：
  - OSC：发到 VRChat 聊天框气泡显示，支持录音时头顶冒出“正在输入”的气泡
  - 键盘：模拟键盘输入并自动复制到剪贴板（这种模式下该工具好像已经可以脱离VRChat了……）
- **AI 润色 / AI 翻译**：很多无言势是因为对自己声音不自信才不敢开麦的……这里的功能就是拿来解决这些的
  - 润色功能可以自动过滤掉一些口吃，结巴的毛病，最后输出一段干净的文本
  - 而翻译也同样接入AI，相较于传统翻译来说更贴合语境，在一些专有名词上也识别的更准

## 多软件适配

- 虽然该软件是为 VRChat 玩家准备的，但其实只要是有聊天框的软件其实基本都能用的（设置中切换到剪贴板模式就好啦）


## 使用方法

**方式一：直接下载（推荐）**

1. 到 [Releases](https://github.com/Tianxiaorui9001/VRCVoice/releases) 下载最新的分发版 zip
2. 解压到任意位置，双击 `VRCVoice.exe` 启动
3. 在设置里选择识别后端：**本地**（离线免费）或 **云端**（填 API Key）

**方式二：源码运行**（面向开发者，见 [开发与配置.md](开发与配置.md)）

## 相关文档

- 📖 [使用说明.md](使用说明.md) —— 详细使用手册（悬浮窗调校 / 常见问题 / 卸载）
- 🔧 [开发与配置.md](开发与配置.md) —— 源码构建、全部配置项、模型说明、目录结构（面向开发者）

## License

[MIT](LICENSE) © 2026 天小锐
