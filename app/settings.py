"""设置模块：所有可配置项集中在此，持久化到 config.json。
原则：不做任何硬编码假设，每项都可在设置面板修改。
"""
import json
import os
import copy

from .log import app_base_dir, data_dir

APP_DIR = app_base_dir()
CONFIG_PATH = os.path.join(data_dir(), "config.json")

DEFAULTS = {
    "general": {
        "language": "zh-en",          # 识别语言: zh / en / zh-en
        "ui_lang": "zh-CN",           # 界面语言: zh-CN / zh-TW / en-US / ja
        "theme": "auto",              # auto / light / dark
        "start_minimized": False,     # 启动后最小化到托盘
        "tray_enabled": True,         # 启用系统托盘
        "show_overlay_in_vr": True,   # VR 悬浮窗开关
    },
    "recognition": {
        "backend": "local",            # local=sherpa-onnx本地 / cloud=云端API
        "model_dir": "",              # ASR 模型目录, 空 = 用程序内置默认目录
        "mic_device": "",             # 麦克风设备名, 空 = 系统默认
        "sample_rate": 16000,         # 采样率
        "max_duration_sec": 30,       # 单次录音上限(防误触一直按住)
        "silence_stop_sec": 0.0,      # 静音自动停止(0 = 不启用, 只靠松手)
        "silence_rms_threshold": 0.01,  # 静音判定音量阈值(RMS), 麦底噪大就调高
        "cloud_endpoint": "https://api.siliconflow.cn/v1/audio/transcriptions",
        "cloud_api_key": "",          # OpenAI 兼容 ASR 接口的 Key
        "cloud_model": "FunAudioLLM/SenseVoiceSmall",
        "cloud_language": "",         # 语言提示, 空 = 自动
        "cloud_timeout_sec": 30,
    },
    "trigger": {
        "mode": "hold",               # hold=按住说话 / toggle=按一下开始再按一下结束
        "pc_hotkey": "right_ctrl",    # PC 快捷键 (pynput 键名, 见设置面板提示)
        "release_delay": 0.4,       # 松开后延迟结束识别(秒), 避免句尾语气词被截掉
        "vr_enabled": True,           # VR 控制器触发开关
        "vr_action": "HoldToTalk",    # SteamVR action 名称(可在 SteamVR 绑定界面改键)
    },
    "output": {
        "mode": "osc",                # osc / keyboard(双发已废弃)
        "osc_host": "127.0.0.1",
        "osc_port": 9000,             # VRChat OSC 接收端口
        "osc_typing_indicator": True, # 录音时发送 /chatbox/typing (显示"正在输入")
        "keyboard_type_delay": 0.02,  # 每个字符间隔秒
        "keyboard_use_clipboard": True,  # 键盘模式用剪贴板粘贴(中文可靠)
        "keyboard_enter_send": True,  # 打完字自动回车发送
        "keyboard_auto_chatbox": True,  # 键盘模式自动开 VRChat 聊天框(Y→输入→回车→Esc)
        "desktop_overlay": True,      # 桌面悬浮窗提醒(识别中/已发送), 键盘模式必备
        "not_heard_text": "没听清, 再说一次?",  # 没识别到内容时的提示文本(可自定义)
    },
    "polish": {
        "enabled": False,             # AI 润色开关(可选, 需 API)
        "use_model": "",             # 引用「AI 设置」模型库里的条目名
        "style": "light",             # raw / light / formal / custom
        "custom_prompt": "",        # 自定义润色风格提示词(style=custom 时使用)
        "timeout_sec": 15,            # 兜底超时(实际优先用模型条目自己的)
        # 兼容字段(旧配置迁移用, UI 不再暴露): provider/endpoint/api_key/model/models_cache
        "provider": "",
        "endpoint": "",
        "api_key": "",
        "model": "",
        "models_cache": [],
    },
    "translate": {
        "enabled": False,             # AI 翻译开关: 把润色/识别结果再翻译一遍
        "use_model": "",             # 引用「AI 设置」模型库里的条目名
        "output_mode": "target_only",  # target_only=仅译文 / bilingual=双语对照
        "separator": " / ",            # 双语对照时原文与译文的间隔符
        "target_lang": "英文",         # 目标语言描述(发给模型的提示词)
        # 兼容字段(旧配置迁移用, UI 不再暴露): engine/provider/endpoint/api_key/model/timeout_sec/models_cache/local_*
        "engine": "follow_polish",
        "provider": "custom",
        "endpoint": "",
        "api_key": "",
        "model": "",
        "timeout_sec": 15,
        "models_cache": [],
        "local_endpoint": "http://127.0.0.1:11434/v1/chat/completions",
        "local_model": "",
        "local_api_key": "",
    },
    "ai_models": {
        "list": [],   # 模型库: [{name, kind(cloud/local), provider, endpoint, api_key, model, timeout_sec}]
    },
    "vr_overlay": {
        "enabled": True,
        "scale": 1.0,                 # 悬浮窗缩放
        "x": 0.5, "y": 0.5,           # 位置(相对视野中心)
        "width_px": 800, "height_px": 300,
        "show_on_record": True,       # 录音时自动显示
        "auto_hide_sec": 4,           # 发送后几秒自动隐藏(0=不隐藏)
        "idle_hide_sec": 2,           # 待机/空结果提示几秒自动隐藏(0=不隐藏)
    },
    "debug": {
        "ignore_vrc_check": False,    # 无视 VRChat 进程检测: 关=必须 VRChat 启动才工作; 开=不启动也能测试
        "show_heartbeat_log": False,  # 显示 VR 心跳日志(每 3 秒一条), 默认关避免刷屏
    },
}


class Settings:
    """配置读写。load 时与默认值深度合并，保证新增配置项不会让旧配置崩溃。"""

    def __init__(self, path: str = CONFIG_PATH):
        self.path = path
        self.data = copy.deepcopy(DEFAULTS)
        self._migrate_legacy_config()

    def _migrate_legacy_config(self):
        """旧版 config.json 在 exe 目录: 新位置无配置而旧位置有时复制迁移, 不丢用户设置。"""
        import sys
        from .log import log as _log
        if not getattr(sys, "frozen", False):
            return
        if os.path.exists(self.path):
            return
        legacy = os.path.join(app_base_dir(), "config.json")
        if os.path.exists(legacy):
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                import shutil
                shutil.copy2(legacy, self.path)
                _log(f"[settings] 已迁移旧配置: {legacy} -> {self.path}")
            except Exception as e:
                _log(f"[settings] 配置迁移失败: {e}")

    def load(self):
        saved = None
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8-sig") as f:
                    saved = json.load(f)
                self.data = self._merge(copy.deepcopy(DEFAULTS), saved)
            except Exception as e:
                print(f"[settings] 读取配置失败({e}), 使用默认配置")
                self.data = copy.deepcopy(DEFAULTS)
        else:
            self.data = copy.deepcopy(DEFAULTS)
        self._migrate_ai_models(saved)
        # 配置缺新字段时补齐写回, 保证 config.json 始终完整
        try:
            self.save()
        except Exception:
            pass
        return self

    def _migrate_ai_models(self, saved):
        """旧版 AI 配置(润色/翻译各自一套 provider/key/模型)迁移进模型库 ai_models.list。
        只迁移带真实 Key 的云端配置或真实本地模型配置; 默认占位配置(无 key)不迁移。
        模型库已有条目时跳过(防重复迁移)。"""
        if not saved or not isinstance(saved, dict):
            return
        lst = self.data.setdefault("ai_models", {}).setdefault("list", [])
        if lst:
            return
        # 1) 云端: AI 润色的真实配置(有 Key 才算数)
        ps = saved.get("polish") or {}
        if (ps.get("model") or "").strip() and (ps.get("api_key") or "").strip():
            model = (ps.get("model") or "").strip()
            name = model.split("/")[-1] or "云端模型"
            lst.append({
                "name": name, "kind": "cloud",
                "provider": (ps.get("provider") or "custom").strip() or "custom",
                "endpoint": (ps.get("endpoint") or "").strip(),
                "api_key": (ps.get("api_key") or "").strip(),
                "model": model,
                "timeout_sec": ps.get("timeout_sec", 15) or 15,
            })
            self.data["polish"]["use_model"] = name
            print(f"[settings] 已迁移 AI 润色配置到模型库: {name}")
        # 2) 翻译独立服务商配置(有 Key 才算数)
        ts = saved.get("translate") or {}
        if (ts.get("model") or "").strip() and (ts.get("api_key") or "").strip() \
                and ts.get("engine") == "independent":
            model = (ts.get("model") or "").strip()
            name = (model.split("/")[-1] or "翻译模型") + "-翻译"
            lst.append({
                "name": name, "kind": "cloud",
                "provider": (ts.get("provider") or "custom").strip() or "custom",
                "endpoint": (ts.get("endpoint") or "").strip(),
                "api_key": (ts.get("api_key") or "").strip(),
                "model": model,
                "timeout_sec": ts.get("timeout_sec", 15) or 15,
            })
            self.data["translate"]["use_model"] = name
            print(f"[settings] 已迁移翻译独立服务商到模型库: {name}")
        # 3) 本地模型配置
        if (ts.get("local_model") or "").strip():
            model = (ts.get("local_model") or "").strip()
            name = f"本地 {model}"
            lst.append({
                "name": name, "kind": "local",
                "provider": "openai",
                "endpoint": (ts.get("local_endpoint") or "").strip()
                             or "http://127.0.0.1:11434/v1/chat/completions",
                "api_key": (ts.get("local_api_key") or "").strip(),
                "model": model,
                "timeout_sec": ts.get("timeout_sec", 15) or 15,
            })
            self.data["translate"]["use_model"] = name
            print(f"[settings] 已迁移本地模型到模型库: {name}")

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[settings] 保存配置失败: {e}")

    @staticmethod
    def _merge(base: dict, override: dict) -> dict:
        for k, v in override.items():
            if k in base and isinstance(base[k], dict) and isinstance(v, dict):
                base[k] = Settings._merge(base[k], v)
            else:
                base[k] = v
        return base

    def get(self, section: str, key: str):
        return self.data.get(section, {}).get(key)

    def set(self, section: str, key: str, value):
        self.data.setdefault(section, {})[key] = value
        self.save()  # 每次修改立即落盘, 防丢失

    def reset(self):
        """恢复全部设置到默认值并落盘(调试用, 日志/模型文件不受影响)。"""
        self.data = copy.deepcopy(DEFAULTS)
        self.save()

    def ensure_mic_default(self):
        """麦克风未配置时自动选一个靠谱的物理设备(避开 Pico 等虚拟麦克风), 并持久化。"""
        if not self.get("recognition", "mic_device"):
            try:
                from .recorder import Recorder
                name = Recorder.suggest_default_mic()
                if name:
                    self.set("recognition", "mic_device", name)
                    print(f"[settings] 自动选择麦克风: {name}")
            except Exception as e:
                print(f"[settings] 自动选择麦克风失败: {e}")

    def section(self, section: str) -> dict:
        return self.data.get(section, {})

    def ai_models(self) -> list:
        """模型库条目列表(引用, 直接改后需 save())。"""
        return self.data.setdefault("ai_models", {}).setdefault("list", [])

    def ai_model_names(self) -> list:
        """模型库全部条目名(去空)。"""
        return [m.get("name", "") for m in self.ai_models() if m.get("name")]

    def default_model_dir(self) -> str:
        """模型目录: 配置了就用配置的, 否则用工作目录下 models/"""
        d = self.get("recognition", "model_dir")
        if d:
            return d
        return os.path.join(APP_DIR, "models")
