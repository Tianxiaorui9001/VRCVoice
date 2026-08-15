"""输出模块: OSC 发送到 VRChat chatbox / 剪贴板模式(复制文本, 手动粘贴)。
VRChat OSC 说明:
  /chatbox/input  [text, enter, notify]  -> enter=True 发进聊天频道(官方行为); enter=False 只设头顶气泡
  /chatbox/typing [bool]                 -> 显示"正在输入"状态
不碰聊天框的关键: 聊天框关闭时 VRChat 收到 /chatbox/input 直接发进频道, 输入框一个字符都不动;
只有聊天框自己开着(按了 Y)时消息才会填进输入框显示——所以 OSC 模式全程别开聊天框即可。
剪贴板模式: 不模拟任何按键(不按 Y/不输入/不回车), 只把最终文本复制到剪贴板,
用户到 VRChat 聊天框点"粘贴"按钮发送。
"""
import time
import threading
from pythonosc.udp_client import SimpleUDPClient
from .log import log


class OSCSender:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._client = SimpleUDPClient(host, port)

    def typing(self, on: bool):
        try:
            self._client.send_message("/chatbox/typing", [on])
        except Exception as e:
            log(f"[osc] typing 发送失败: {e}")

    def send_text(self, text: str, notify: bool = True, enter: bool = True):
        try:
            # 官方协议: /chatbox/input [s, b, n] - b=True 直接发送, n=通知音效
            # b=False 只填入聊天输入框不发送(AI 润色中先展示原文)
            # 限 144 字符
            text = text[:144]
            self._client.send_message("/chatbox/input", [text, enter, notify])
            log(f"[osc] 已发送到 {self.host}:{self.port}: {text[:50]} (enter={enter})")
        except Exception as e:
            log(f"[osc] 发送失败: {e}")

    def set_target(self, host: str, port: int):
        if (host, port) != (self.host, self.port):
            self.host, self.port = host, port
            self._client = SimpleUDPClient(host, port)
            log(f"[osc] 目标改为 {host}:{port}")


class ClipboardSender:
    """剪贴板模式: 只把文本复制到剪贴板, 不模拟任何按键。
    VRChat 聊天框自带"粘贴"按钮, 用户手动粘贴发送。"""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled  # 硬隔离: 非剪贴板模式下禁用

    def send_text(self, text: str, enter: bool = True, replace: bool = False):
        if not self.enabled:
            log("[clipboard] 已禁用(当前非剪贴板模式)")
            return
        try:
            import win32clipboard
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
            win32clipboard.CloseClipboard()
            log(f"[clipboard] 已复制到剪贴板: {text[:50]}")
        except Exception as e:
            log(f"[clipboard] 复制失败: {e}")


class OutputManager:
    """按设置把文本发出去。mode: osc / keyboard(剪贴板模式, 双发已废弃, 未知值兜底 OSC)"""

    def __init__(self, settings):
        self.settings = settings
        self.osc = OSCSender(
            settings.get("output", "osc_host"),
            settings.get("output", "osc_port"),
        )
        self.cb = ClipboardSender(
            enabled=(settings.get("output", "mode") == "keyboard"),
        )
        self._lock = threading.Lock()

    def _ensure_mode(self):
        """发送前确保发送器与当前模式一致(UI 切模式后自动重建, 防误用)。"""
        want = (self.settings.get("output", "mode") == "keyboard")
        if self.cb.enabled != want:
            self.refresh()

    def osc_test(self) -> str:
        """OSC 自检: 发一条测试消息, 返回结果描述。"""
        try:
            self.osc.send_text("VRCVoice 测试 OK ✓", notify=True, enter=True)
            return "测试消息已发送, 去 VRChat 看聊天频道有没有消息"
        except Exception as e:
            log(f"[osc] 测试失败: {e}")
            return f"发送失败: {e}"

    def on_recording_started(self):
        s = self.settings
        if s.get("output", "osc_typing_indicator"):
            with self._lock:
                self.osc.typing(True)

    def on_recording_stopped(self):
        # 不关 typing: 松手后润色期间保持"正在输入", 直到消息真正发出(send/send_final 里关)
        pass

    def send(self, text: str):
        if not text.strip():
            return
        s = self.settings
        mode = s.get("output", "mode")
        with self._lock:
            if mode == "keyboard":
                self._ensure_mode()
                threading.Thread(target=self.cb.send_text, args=(text,), daemon=True).start()
            else:  # osc / 未知模式一律走 OSC, 不碰键盘
                # enter=True 发进聊天频道, notify=False 不弹气泡(用户明确不要气泡)
                self.osc.send_text(text, notify=False, enter=True)
                if s.get("output", "osc_typing_indicator"):
                    self.osc.typing(False)  # 消息已发出, 关闭"正在输入"
        log(f"[send] 模式={mode} 文本={text[:50]}")

    def send_draft(self, text: str):
        """AI 润色中: 不提前输出原文(只输出润色版)。
        剪贴板模式: 润色完直接复制润色版; OSC 模式: 润色完直接发送。"""
        if not text.strip():
            return
        mode = self.settings.get("output", "mode")
        log(f"[send] 润色中: 不提前输出原文, 完成后直接发润色版 模式={mode}")

    def send_final(self, text: str):
        """AI 润色完成: 替换输入框里的原文并发送。"""
        if not text.strip():
            return
        s = self.settings
        mode = s.get("output", "mode")
        with self._lock:
            if mode == "keyboard":
                self._ensure_mode()
                self.cb.send_text(text, enter=True)
            else:  # osc / 未知模式一律走 OSC
                # enter=True 发进聊天频道, notify=False 不弹气泡(用户明确不要气泡)
                self.osc.send_text(text, notify=False, enter=True)
                if s.get("output", "osc_typing_indicator"):
                    self.osc.typing(False)  # 消息已发出, 关闭"正在输入"
        log(f"[send] 润色完成发送 模式={mode} 文本={text[:50]}")

    def refresh(self):
        """设置变更后重建发送器。"""
        s = self.settings
        self.osc.set_target(s.get("output", "osc_host"), s.get("output", "osc_port"))
        self.cb = ClipboardSender(
            enabled=(s.get("output", "mode") == "keyboard"),
        )
