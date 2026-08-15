"""全局热键: pynput 监听, 支持 hold(按住说话)/toggle(按一下开始,再按结束)。
支持组合键(如 ctrl+shift+g): 全部按住才触发, 松开任一即停。
键识别统一走 key_to_name(基于 VK 物理键), 不受修饰键影响(避免 Ctrl+Shift+G
变成控制字符导致永远匹配不上)。录制快捷键时用 SUPPRESSED 屏蔽触发。
"""
import threading
import time
from pynput import keyboard

# 录制快捷键时置 True, 暂时屏蔽触发(避免边录边触发录音)
SUPPRESSED = False

# pynput Key -> 键名(稳定, 配置里存的格式)
_KEY_NAMES = {
    keyboard.Key.ctrl_l: "left_ctrl", keyboard.Key.ctrl_r: "right_ctrl",
    keyboard.Key.alt_l: "left_alt", keyboard.Key.alt_r: "right_alt",
    keyboard.Key.shift_l: "left_shift", keyboard.Key.shift_r: "right_shift",
    keyboard.Key.space: "space", keyboard.Key.enter: "enter",
    keyboard.Key.tab: "tab", keyboard.Key.caps_lock: "caps_lock",
    keyboard.Key.backspace: "backspace", keyboard.Key.home: "home",
    keyboard.Key.end: "end", keyboard.Key.insert: "insert",
    keyboard.Key.delete: "delete", keyboard.Key.page_up: "page_up",
    keyboard.Key.page_down: "page_down", keyboard.Key.up: "up",
    keyboard.Key.down: "down", keyboard.Key.left: "left",
    keyboard.Key.right: "right",
    keyboard.Key.f1: "f1", keyboard.Key.f2: "f2", keyboard.Key.f3: "f3",
    keyboard.Key.f4: "f4", keyboard.Key.f5: "f5", keyboard.Key.f6: "f6",
    keyboard.Key.f7: "f7", keyboard.Key.f8: "f8", keyboard.Key.f9: "f9",
    keyboard.Key.f10: "f10", keyboard.Key.f11: "f11", keyboard.Key.f12: "f12",
}


def _vk_name(vk: int):
    """虚拟键码 -> 键名。物理键识别, 与是否按住修饰键无关。"""
    if 0x41 <= vk <= 0x5A:      # A-Z
        return chr(vk + 0x20)
    if 0x30 <= vk <= 0x39:      # 0-9
        return chr(vk)
    if 0x70 <= vk <= 0x7B:      # F1-F12
        return f"f{vk - 0x70 + 1}"
    m = {
        0x20: "space", 0x0D: "enter", 0x09: "tab", 0x1B: "esc",
        0x14: "caps_lock", 0x08: "backspace", 0x2D: "insert", 0x2E: "delete",
        0x21: "page_up", 0x22: "page_down", 0x24: "home", 0x23: "end",
        0x25: "left", 0x26: "up", 0x27: "right", 0x28: "down",
        0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".", 0xBF: "/",
        0xC0: "`", 0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'",
    }
    return m.get(vk)


def key_to_name(key):
    """把 pynput 键事件转成稳定键名(配置里存的格式)。返回 None 表示不支持的键。"""
    if isinstance(key, keyboard.KeyCode):
        if key.vk is not None:
            name = _vk_name(key.vk)
            if name:
                return name
        if key.char and key.char.isprintable() and ord(key.char) >= 0x20:
            return key.char.lower()
        return None
    return _KEY_NAMES.get(key)


def parse_key(name: str):
    """把配置字符串解析成键名列表。支持单键或组合(用 + 连接)。"""
    name = name.strip().lower()
    parts = [p.strip() for p in name.split("+") if p.strip()]
    if not parts:
        raise ValueError("空快捷键")
    for p in parts:
        if p not in _KEY_NAMES.values() and len(p) != 1:
            raise ValueError(f"无法识别的键: {p}")
    return parts


class HotkeyListener:
    def __init__(self, key_name: str, mode: str,
                 on_start, on_stop, on_toggle=None, release_delay: float = 0.0,
                 on_is_recording=None):
        """on_is_recording: 可选回调返回当前是否正在录音。
        toggle 模式用它自愈状态漂移(静音自动停止/VR 停止不经过 toggle
        翻转, 会导致按热键翻到"停止"而看似无反应)。"""
        self.key_name = key_name
        self.mode = mode
        self.on_start = on_start
        self.on_stop = on_stop
        self.on_toggle = on_toggle
        self.on_is_recording = on_is_recording
        self.release_delay = max(0.0, float(release_delay))
        self._listener = None
        self._pressed = False
        self._toggle_active = False
        self._held = set()
        self._target = self._parse_target()
        self._stop_timer = None
        self._last_flip = 0.0

    def update(self, key_name=None, mode=None, release_delay=None):
        """热键参数热更新, 不重启底层 listener。
        pynput 的 stop() 是异步的(win32 hook 线程不会立即退出), 快速
        stop+start 重建会新旧两个 hook 并存 → 一次按键被处理两次 →
        toggle 状态互踩 → start/stop 交替风暴甚至崩溃。所以改键/改模式
        只更新参数, listener 线程常驻。"""
        if key_name is not None:
            self.key_name = key_name
            self._target = self._parse_target()
        if mode is not None:
            self.mode = mode
        if release_delay is not None:
            self.release_delay = max(0.0, float(release_delay))
        # 重置按键状态, 防止旧键的按下/翻转状态残留到新键
        self._pressed = False
        self._held.clear()
        self._toggle_active = False
        self._cancel_pending_stop()

    def _parse_target(self):
        try:
            return parse_key(self.key_name)
        except ValueError:
            return None

    def _is_combo(self) -> bool:
        return bool(self._target) and len(self._target) > 1

    def _cancel_pending_stop(self):
        if self._stop_timer:
            self._stop_timer.cancel()
            self._stop_timer = None

    def _trigger_start(self):
        had_pending = self._stop_timer is not None
        self._cancel_pending_stop()  # 延迟窗口内重新按住 -> 无缝续录
        if self._pressed:
            if had_pending:
                return  # release_delay 窗口内的快速再按: 录音未断, 无缝续录
            # release 事件丢失(pynput 钩子偶发) → 自愈: 停掉残留录音, 重新开始
            # 否则 _pressed 永久滞留, 后续按下全被吞 → "按下不理我"
            if self.on_is_recording is not None and self.on_is_recording():
                try:
                    self.on_stop()  # 同步停, 避免与下方 start 并发抢锁
                except Exception:
                    pass
            self._pressed = False
        self._pressed = True
        threading.Thread(target=self.on_start, daemon=True).start()

    def _trigger_stop(self):
        self._cancel_pending_stop()
        if self._pressed:
            self._pressed = False
            threading.Thread(target=self.on_stop, daemon=True).start()

    def _schedule_stop(self):
        """按住模式松开后延迟结束, 给句尾语气词留时间。
        期间重新按住会取消延迟(见 _trigger_start)。"""
        if self.release_delay > 0:
            self._cancel_pending_stop()
            t = threading.Timer(self.release_delay, self._trigger_stop)
            t.daemon = True
            self._stop_timer = t
            t.start()
        else:
            self._trigger_stop()

    def _toggle_flip(self):
        """toggle 模式: 以实际录音状态为准决定开/停, 不盲翻标志位。
        修复: 静音自动停止/VR 停止不经 toggle 翻转导致状态漂移 →
        "已发送"后按热键翻到"停止"看似无反应, 必须按两次才生效。"""
        now = time.time()
        if now - self._last_flip < 0.15:
            return  # 防抖: 双 hook 并存期的事件重放会在同一瞬间重复触发
        self._last_flip = now
        if self.on_is_recording is not None:
            if self.on_is_recording():
                self._toggle_active = False
                self._trigger_stop()
            else:
                self._toggle_active = True
                self._trigger_start()
        else:
            self._toggle_active = not self._toggle_active
            if self._toggle_active:
                self._trigger_start()
            else:
                self._trigger_stop()
        if self.on_toggle:
            self.on_toggle(self._toggle_active)

    def start(self):
        self._listener = keyboard.Listener(
            on_press=self._on_press, on_release=self._on_release)
        self._listener.daemon = True
        self._listener.start()

    def stop(self):
        self._cancel_pending_stop()
        if self._listener:
            self._listener.stop()
            self._listener = None

    def _on_press(self, key):
        if SUPPRESSED:
            return
        if not self._target:
            return
        name = key_to_name(key)
        if name is None:
            return
        if self._is_combo():
            if name not in self._target:
                return
            self._held.add(name)
            if all(k in self._held for k in self._target):
                if self.mode == "hold":
                    self._trigger_start()
                else:
                    self._toggle_flip()
        else:
            if name != self._target[0]:
                return
            if self.mode == "hold":
                self._trigger_start()
            else:
                self._toggle_flip()

    def _on_release(self, key):
        if SUPPRESSED:
            return
        if not self._target:
            return
        name = key_to_name(key)
        if name is None:
            return
        if self._is_combo():
            if name not in self._target:
                return
            self._held.discard(name)
            if self.mode == "hold" and self._pressed and not all(k in self._held for k in self._target):
                self._schedule_stop()
        else:
            if name == self._target[0] and self.mode == "hold":
                self._schedule_stop()
