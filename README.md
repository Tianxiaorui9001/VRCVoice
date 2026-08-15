# VRCVoice - VRChat 语音输入助手

给"无言势"准备的：按住按键说话，松手即把识别出的文字发给 VRChat（chatbox 显示或真实键盘输入）。
本地识别（sherpa-onnx），不联网也能用；识别模型与 OpenLess 同款引擎技术。

## 启动

```
start.bat
```
或手动：
```
.venv\Scripts\python.exe main.py
```

## 功能

- 本地 / 云端识别后端可切换：
  - local：sherpa-onnx 本地流式识别（离线，占用约 500MB 模型空间）
  - cloud：OpenAI 兼容 ASR 接口（硅基流动 / OpenAI 等），不占空间，适合低配/空间紧张的机器
- PC：全局快捷键（默认右 Ctrl）按住说话，松手发送；可在设置里改键
- VR（Phase 2）：SteamVR 可自定义按键（action manifest，像 OVRAS 一样在 SteamVR 绑定界面改键）；悬浮窗显示状态与文字
- 输出双模式（设置切换）：
  - OSC：发送到 VRChat chatbox（头顶气泡显示），录音时显示"正在输入"
  - 键盘：模拟键盘输入真实发进聊天框（需要聊天输入框已获得焦点），可选自动回车
- 可选 AI 润色（OpenAI 兼容接口，如硅基流动），默认关闭
- 全参数设置面板（Fluent Design），全部持久化在 config.json

## 配置

所有配置在 `config.json`（首次运行自动生成），GUI 设置面板修改即保存。主要项：

| 配置 | 说明 |
|---|---|
| recognition.backend | local(本地模型) / cloud(云端API) |
| recognition.cloud_endpoint | 云端 ASR 接口(默认硅基流动 OpenAI 兼容) |
| recognition.cloud_api_key | 云端 API Key |
| recognition.cloud_model | 云端模型, 如 FunAudioLLM/SenseVoiceSmall |
| general.language | 识别语言 zh / en / zh-en (本地模式) |
| recognition.model_dir | 本地模型目录（默认 models/） |
| recognition.mic_device | 麦克风设备名（VR 时可选 PicoStreamingMicrophone） |
| recognition.max_duration_sec | 单次录音上限（防误触） |
| recognition.silence_stop_sec | 静音自动停止（0=关） |
| trigger.mode | hold / toggle |
| trigger.pc_hotkey | pynput 键名，如 right_ctrl、f8、ctrl+shift+v |
| trigger.vr_action | SteamVR action 名 |
| output.mode | osc / keyboard / both |
| output.osc_host / osc_port | VRChat OSC（默认 127.0.0.1:9000） |
| output.osc_typing_indicator | 录音时 /chatbox/typing |
| output.keyboard_enter_send | 键盘模式自动回车 |
| polish.* | 可选润色 API |

## 模型

默认模型：sherpa-onnx-streaming-zipformer-bilingual-zh-en（中英双语流式，int8，约 280MB）。
下载脚本：
```
models\download_model.bat
```
模型文件需为：encoder-epoch-99-avg-1.int8.onnx / decoder-epoch-99-avg-1.onnx /
joiner-epoch-99-avg-1.int8.onnx / tokens.txt

## 目录结构

```
VRCVoice/
├─ main.py              入口
├─ config.json          配置(自动生成)
├─ models/              ASR 模型
├─ resources/           SteamVR action manifest + 绑定文件
└─ app/
   ├─ settings.py       配置读写
   ├─ recorder.py       录音
   ├─ asr_engine.py     sherpa-onnx 流式识别
   ├─ output.py         OSC / 键盘输出
   ├─ hotkey.py         PC 全局热键
   ├─ vr_input.py       OpenVR 输入(Phase 2)
   ├─ controller.py     识别流程控制器
   └─ gui/              Fluent 设置面板
```

## 已知限制

- OSC chatbox 只是显示文本，不会真的发进聊天频道（VRChat 限制）；真发送用键盘模式
- 键盘模式需要聊天输入框已获得焦点
- VR 悬浮窗/控制器为 Phase 2，界面骨架已就位
