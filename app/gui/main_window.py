"""所有设置项都绑定 Settings 对象, 修改即保存, 无硬编码。"""
import os
import sys
import threading
import time

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QUrl
from PySide6.QtGui import QPixmap, QDesktopServices
from PySide6.QtWidgets import (QFileDialog, QLabel, QVBoxLayout, QWidget,
                               QHBoxLayout, QScrollArea, QGridLayout, QFrame,
                               QDialog, QFormLayout, QSpinBox, QMessageBox,
                               QPlainTextEdit,
                               QInputDialog)

from qfluentwidgets import (
    FluentWindow, NavigationItemPosition, FluentIcon, SettingCard,
    SettingCardGroup, SwitchButton, ComboBox, LineEdit, PrimaryPushButton,
    PushButton, InfoBar, InfoBarPosition, CardWidget, BodyLabel,
    CaptionLabel, setTheme, Theme, StrongBodyLabel, ExpandGroupSettingCard,
    SmoothScrollArea, IconWidget, ProgressBar,
)

from ..settings import Settings, APP_BANNER
from ..controller import RecognitionController
from .. import autostart
from ..i18n import tr
from ..update_checker import (local_version, check_latest, download_release,
                              update_dir, RELEASE_URL)
from .log_page import LogPage

# 更新安装脚本(ASCII): 路径在生成时写死进 bat(不依赖 cmd /c 参数传递, 绕开引号解析坑)
# 每步写 install_trace.log, 主程序 os._exit 后仍可查安装进展/失败原因
_UPDATER_BAT = r'''@echo off
rem VRCVoice updater: paths baked in
set "ZIP={zip}"
set "DST={dst}"
set "TMP={tmp}"
set "TRACE={trace}"
echo [0] start %date% %time% >> "%TRACE%"
if "%ZIP%"=="" goto :fail
rem wait for main proc to exit (ping instead of timeout: timeout errors on redirected stdin)
ping -n 4 127.0.0.1 >nul
echo [1] waited >> "%TRACE%"
taskkill /IM VRCVoice.exe /F /T >nul 2>nul
echo [2] taskkill done >> "%TRACE%"
if exist "%TMP%" rmdir /s /q "%TMP%"
mkdir "%TMP%"
echo [3] mkdir done >> "%TRACE%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "try {{ Expand-Archive -LiteralPath '{zip}' -DestinationPath '{tmp}' -Force; exit 0 }} catch {{ exit 1 }}"
if errorlevel 1 goto :fail
echo [4] extracted >> "%TRACE%"
set "SRC="
if exist "%TMP%\VRCVoice\VRCVoice.exe" set "SRC=%TMP%\VRCVoice"
if not defined SRC if exist "%TMP%\VRCVoice.exe" set "SRC=%TMP%"
if not defined SRC goto :fail
echo [5] src=%SRC% >> "%TRACE%"
if not exist "%DST%" mkdir "%DST%"
robocopy "%SRC%" "%DST%" /E /MOVE /MIR /NFL /NDL /NJH /NJS /NP >nul
set RC=%errorlevel%
echo [6] robocopy rc=%RC% >> "%TRACE%"
if %RC% GEQ 8 goto :fail
start "" "%DST%\VRCVoice.exe"
echo [7] launched >> "%TRACE%"
del /q "%ZIP%" 2>nul
rmdir /s /q "%TMP%" 2>nul
echo [8] cleaned done >> "%TRACE%"
exit /b 0
:fail
echo [FAIL] %date% %time% zip=%ZIP% dst=%DST% tmp=%TMP% >> "%TRACE%"
exit /b 1
'''


class VRCSettingCardGroup(SettingCardGroup):
    """修复: ExpandLayout 在 FluentWindow 嵌套布局里不会自动 setGeometry, 导致卡片全叠在原点。
    在尺寸变化/显示时强制重排。"""

    def _force_relayout(self):
        try:
            rect = self.cardLayout.geometry()
            if rect.width() <= 0:
                rect.setWidth(self.width())
            if rect.height() <= 0:
                rect.setHeight(max(0, self.height() - 46))
            self.cardLayout.setGeometry(rect)
            self.layout().activate()
        except Exception:
            pass

    def clear(self):
        """清空全部卡片。ExpandLayout 没有 removeWidget, addWidget 也不排布,
        旧卡片 deleteLater 后仍残留悬垂引用导致重排中断 -> 直接换新 QVBoxLayout。"""
        old = self.cardLayout
        for w in list(getattr(old, "_ExpandLayout__widgets", [])):
            try:
                w.setParent(None)
            except Exception:
                pass
            try:
                w.deleteLater()
            except Exception:
                pass
        self.vBoxLayout.removeItem(old)
        nl = QVBoxLayout()
        nl.setContentsMargins(0, 0, 0, 0)
        nl.setSpacing(2)
        self.vBoxLayout.addLayout(nl, 1)
        self.cardLayout = nl
        self._force_relayout()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._force_relayout()

    def showEvent(self, e):
        super().showEvent(e)
        self._force_relayout()


# AI 润色服务商预设, 选中自动填 endpoint, 模型列表自动获取(三种 API 格式)

# ---------- 自定义设置卡(不依赖 qconfig, 直接读写我们的 Settings) ----------
PROVIDERS = {
    "deepseek": {
        "name": "DeepSeek",
        "endpoint": "https://api.deepseek.com/chat/completions",
        "models_url": "https://api.deepseek.com/models",
        "kind": "openai",
    },
    "siliconflow": {
        "name": "硅基流动",
        "endpoint": "https://api.siliconflow.cn/v1/chat/completions",
        "models_url": "https://api.siliconflow.cn/v1/models",
        "kind": "openai",
    },
    "openai": {
        "name": "OpenAI (ChatGPT)",
        "endpoint": "https://api.openai.com/v1/chat/completions",
        "models_url": "https://api.openai.com/v1/models",
        "kind": "openai",
    },
    "anthropic": {
        "name": "Claude",
        "endpoint": "https://api.anthropic.com/v1/messages",
        "models_url": "https://api.anthropic.com/v1/models",
        "kind": "anthropic",
    },
    "gemini": {
        "name": "Gemini",
        "endpoint": "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        "models_url": "https://generativelanguage.googleapis.com/v1beta/models?key={key}",
        "kind": "gemini",
    },
}

class _BaseCard(SettingCard):
    def __init__(self, title, content, parent=None, icon=None):
        super().__init__(icon or FluentIcon.SETTING, title, content, parent)
        self.hBoxLayout.setContentsMargins(48, 12, 48, 12)


class ComboCard(_BaseCard):
    """下拉卡片: 显示中文, 内部存原始值。value_map = {原始值: 显示文本}"""

    def __init__(self, title, content, items, getter, setter, value_map=None, parent=None, icon=None):
        super().__init__(title, content, parent, icon)
        self._after = lambda t: None
        self._setter = setter
        self._value_map = value_map or {}
        self._reverse_map = {v: k for k, v in self._value_map.items()}
        self.combo = ComboBox(self)
        self.combo.addItems(items)
        self.hBoxLayout.addWidget(self.combo, 0, Qt.AlignmentFlag.AlignRight)
        raw = getter()
        display = self._value_map.get(raw, raw)
        if display in items:
            self.combo.setCurrentText(display)
        self.combo.currentTextChanged.connect(self._on_change)

    def _on_change(self, display: str):
        raw = self._reverse_map.get(display, display)
        self._setter(raw)
        self._after(raw)

    def set_after_change(self, fn):
        self._after = fn


class SwitchCard(_BaseCard):
    def __init__(self, title, content, getter, setter, parent=None, icon=None):
        super().__init__(title, content, parent, icon)
        self._after = lambda v: None
        self.switch = SwitchButton(self)
        self.switch.setOnText(tr("开"))
        self.switch.setOffText(tr("关"))
        self.switch.setChecked(bool(getter()))
        self.hBoxLayout.addWidget(self.switch, 0, Qt.AlignmentFlag.AlignRight)
        self.switch.checkedChanged.connect(lambda v: (setter(v), self._after(v)))

    def set_after_change(self, fn):
        self._after = fn


class LineCard(_BaseCard):
    def __init__(self, title, content, getter, setter, placeholder="", password=False, parent=None, icon=None):
        super().__init__(title, content, parent, icon)
        self._after = lambda: None
        self.edit = LineEdit(self)
        self.edit.setPlaceholderText(placeholder)
        self.edit.setText(str(getter()))
        self.edit.setFixedWidth(220)
        if password:
            from PySide6.QtWidgets import QLineEdit
            self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.hBoxLayout.addWidget(self.edit, 0, Qt.AlignmentFlag.AlignRight)
        self.edit.editingFinished.connect(lambda: (setter(self.edit.text()), self._after()))

    def set_after_change(self, fn):
        self._after = fn


class _PromptEdit(QPlainTextEdit):
    """多行输入框: 失焦时通知保存。"""
    editing_done = Signal()

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.editing_done.emit()


class TextAreaCard(_BaseCard):
    """多行文本卡片: 支持换行写长提示词; 输入停顿 800ms 防抖保存, 失焦立即保存。"""

    def __init__(self, title, content, getter, setter, placeholder="", parent=None, icon=None):
        super().__init__(title, content, parent, icon)
        self._after = lambda: None
        self._setter = setter
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(800)
        self._save_timer.timeout.connect(self._save)
        self.edit = _PromptEdit(self)
        self.edit.setPlaceholderText(placeholder)
        self.edit.setPlainText(str(getter()))
        # 只固定高度, 宽度随布局伸缩; minimumWidth 兜底窄窗口可用 —— 不会把卡片挤出画面
        self.edit.setFixedHeight(110)
        self.edit.setMinimumWidth(260)
        from PySide6.QtWidgets import QSizePolicy
        self.edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.hBoxLayout.addWidget(self.edit, 1, Qt.AlignmentFlag.AlignRight)
        self.edit.editing_done.connect(self._save)
        self.edit.textChanged.connect(self._save_timer.start)
        self.setFixedHeight(150)

    def _save(self):
        self._save_timer.stop()
        self._setter(self.edit.toPlainText())
        self._after()

    def set_after_change(self, fn):
        self._after = fn


class HotkeyCard(_BaseCard):
    """录制式快捷键: 点击后按下组合键, 实时显示, 全部松开自动保存。"""
    live = Signal(str)      # 实时显示(仅更新按钮文本, 不动监听)
    captured = Signal(object)  # 录制结束(保存)

    KEY_DISPLAY = {
        "left_ctrl": tr("左 Ctrl"), "right_ctrl": tr("右 Ctrl"), "ctrl": "Ctrl",
        "left_alt": tr("左 Alt"), "right_alt": tr("右 Alt"), "alt": "Alt",
        "left_shift": tr("左 Shift"), "right_shift": tr("右 Shift"), "shift": "Shift",
        "space": tr("空格"), "enter": tr("回车"), "tab": "Tab", "esc": "Esc",
        "caps_lock": tr("大写锁定"), "backspace": tr("退格"), "home": "Home", "end": "End",
        "insert": "Insert", "delete": "Del", "page_up": "PgUp", "page_down": "PgDn",
        "up": tr("上"), "down": tr("下"), "left": tr("左"), "right": tr("右"),
    }
    _MOD_ORDER = ["left_ctrl", "right_ctrl", "left_alt", "right_alt",
                  "left_shift", "right_shift"]

    def __init__(self, title, content, getter, setter, parent=None, icon=None):
        super().__init__(title, content, parent, icon)
        self.getter = getter
        self.setter = setter
        self.btn = PushButton(self._display(getter() or tr("未设置")))
        self.btn.setFixedWidth(220)
        self.hBoxLayout.addWidget(self.btn, 0, Qt.AlignmentFlag.AlignRight)
        self.btn.clicked.connect(self._toggle_capture)
        self.live.connect(self.btn.setText)
        self.captured.connect(self._on_captured)
        self._listener = None
        self._held = set()
        self._chord = ""
        self._active = False

    @classmethod
    def _display(cls, raw: str) -> str:
        if not raw:
            return tr("未设置")
        parts = [cls.KEY_DISPLAY.get(p, p) for p in raw.split("+")]
        parts = [p.upper() if len(p) == 1 and p.isalpha() else p for p in parts]
        return "+".join(parts)

    @classmethod
    def _order(cls, names):
        mods = [m for m in cls._MOD_ORDER if m in names]
        rest = sorted(n for n in names if n not in cls._MOD_ORDER)
        return mods + rest

    @staticmethod
    def _raw_name(key):
        """name"""
        from .. import hotkey as hotkey_mod
        return hotkey_mod.key_to_name(key)

    def _toggle_capture(self):
        if self._active:
            self._end_capture(save=False)
        else:
            self._start_capture()

    def _start_capture(self):
        from pynput import keyboard
        from .. import hotkey as hotkey_mod
        self._held = set()
        self._chord = ""
        self._active = True
        self.btn.setText(tr("请按下新的快捷键... (Esc 取消)"))
        hotkey_mod.SUPPRESSED = True  # 录制时屏蔽旧热键

        def on_press(key):
            if key == keyboard.Key.esc:
                self.captured.emit(None)
                return False
            name = self._raw_name(key)
            if not name:
                return
            self._held.add(name)
            self._chord = "+".join(self._order(self._held))
            self.live.emit(self._display(self._chord))

        def on_release(key):
            name = self._raw_name(key)
            if not name:
                return
            if name not in self._held:
                return  # 忽略按下前就在按的键, 避免误结束
                self._held.discard(name)
            if self._held:
                self.live.emit(self._display("+".join(self._order(self._held))))
            elif self._chord:
                self.captured.emit(self._chord)  # 全部松开: 保存累积的组合
        self._listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self._listener.daemon = True
        self._listener.start()

    def _end_capture(self, save: bool):
        if self._listener:
            self._listener.stop()
            self._listener = None
        self._active = False
        try:
            from .. import hotkey as hotkey_mod
            hotkey_mod.SUPPRESSED = False
        except Exception:
            pass

    def _on_captured(self, chord):
        self._end_capture(save=bool(chord))
        if chord:
            self.setter(chord)
            self.btn.setText(self._display(chord))
        else:
            self.btn.setText(self._display(self.getter() or ""))

class NumberCard(_BaseCard):
    def __init__(self, title, content, getter, setter, lo=0, hi=100000, step=1, parent=None, icon=None):
        super().__init__(title, content, parent, icon)
        self._after = lambda: None
        # Fluent 风格数字输入(qfluentwidgets), 替换 Windows 经典 QSpinBox 样式
        from qfluentwidgets import SpinBox as FluentSpinBox
        from qfluentwidgets import DoubleSpinBox as FluentDoubleSpinBox
        self.spin = FluentDoubleSpinBox(self) if isinstance(step, float) else FluentSpinBox(self)
        self.spin.setRange(float(lo), float(hi))
        self.spin.setSingleStep(float(step))
        if isinstance(step, float):
            self.spin.setDecimals(2)
        self.spin.setValue(float(getter()))
        self.spin.setFixedWidth(120)
        self.hBoxLayout.addWidget(self.spin, 0, Qt.AlignmentFlag.AlignRight)
        self.spin.valueChanged.connect(lambda v: (setter(float(v) if isinstance(step, float) else int(v)), self._after()))

    def set_after_change(self, fn):
        self._after = fn


class ButtonCard(_BaseCard):
    def __init__(self, title, content, text, on_click, primary=False, parent=None, icon=None):
        super().__init__(title, content, parent, icon)
        btn = PrimaryPushButton(text, self) if primary else PushButton(text, self)
        # clicked 信号带 checked 参数; lambda 必须显式接住, 否则 PySide6 按 co_argcount
        # 传参覆盖默认值(如 lambda _m=m 会被 False 覆盖 -> 编辑对话框空白)
        btn.clicked.connect(lambda _=False: on_click())
        self.hBoxLayout.addWidget(btn, 0, Qt.AlignmentFlag.AlignRight)


class CloudModelComboCard(_BaseCard):
    """云端识别模型: 从模型库下拉选择(名称); 无模型时禁用并显示占位。"""

    def __init__(self, title, content, on_pick, parent=None, icon=None):
        super().__init__(title, content, parent, icon)
        self.combo = ComboBox(self)
        self.combo.setFixedWidth(220)
        self.combo.currentTextChanged.connect(on_pick)
        self.hBoxLayout.addWidget(self.combo, 0, Qt.AlignmentFlag.AlignRight)

    def set_models(self, names, current=""):
        self.combo.blockSignals(True)
        self.combo.clear()
        if names:
            self.combo.addItems(names)
            if current in names:
                self.combo.setCurrentText(current)
        else:
            self.combo.addItem(tr("(未配置模型)"))
        self.combo.setEnabled(bool(names))
        self.combo.blockSignals(False)


class ModelListCard(_BaseCard):
    """LineCard"""

    CUSTOM = "自定义"

    def __init__(self, title, content, on_pick, on_fetch=None, parent=None, icon=None):
        super().__init__(title, content, parent, icon)
        self.combo = ComboBox(self)
        self.combo.addItem(self.CUSTOM)
        self.combo.setEnabled(False)
        self.combo.setFixedWidth(200)
        self.combo.currentTextChanged.connect(on_pick)
        self.btn = None
        if on_fetch:
            self.btn = PushButton(tr("获取模型"), self)
            self.btn.setFixedWidth(92)
            self.btn.clicked.connect(on_fetch)
            self.hBoxLayout.addWidget(self.btn, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addWidget(self.combo, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.setSpacing(8)

    def set_models(self, models, current=""):
        """填入获取到的模型列表; 当前模型在列表里则选中, 否则保持'自定义'(手输)。"""
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItems([self.CUSTOM] + list(models))
        if current and current in models:
            self.combo.setCurrentText(current)
        else:
            self.combo.setCurrentText(self.CUSTOM)
        self.combo.setEnabled(bool(models))
        self.combo.blockSignals(False)


class DebugStatusCard(CardWidget):
    """调试区实时状态卡: 每 2 秒刷新 SteamVR/VRChat/悬浮窗状态。
    查询全部在后台线程执行(tasklist / openvr 都可能卡, 实测主线程直接查
    tasklist 会因子进程卡死而 AppHang), 主线程只接收信号更新文本。"""

    _done = Signal(str)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.label = CaptionLabel(tr("获取中..."))
        self.label.setWordWrap(True)
        self.label.setStyleSheet("font-family: Consolas; font-size: 12px;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.addWidget(self.label)
        self._busy = False
        self._done.connect(self._apply)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(2000)
        self._tick()

    def _apply(self, text):
        self.label.setText(text)
        self._busy = False

    def _tick(self):
        if self._busy:
            return  # 上一轮没查完就跳过, 防线程堆积
        self._busy = True
        threading.Thread(target=self._query, daemon=True).start()

    def _query(self):
        """后台线程: 慢查询(tasklist / openvr)全部在这里, 主线程绝不接触。"""
        try:
            from ..vrc_status import vrc_status, steamvr_running
            from ..vr_overlay import get_overlay
            vr = tr("运行中") if steamvr_running() else tr("未运行")
            vc = vrc_status()
            ov = get_overlay()
            if ov is None:
                ovl = tr("未创建")
            else:
                st = ov.status()  # 50ms 超时兜底, 后台线程内安全
                if st["ok"]:
                    vis = tr("可见") if st["visible"] else tr("隐藏")
                    ovl = tr("{vis} 宽{width}m α{alpha}", vis=vis, width=st["width_m"], alpha=st["alpha"])
                    if st.get("err"):
                        ovl += f" | {st['err'][:24]}"
                else:
                    ovl = tr("未创建")
            self._done.emit(f"   VRChat: ")
        except Exception as e:
            self._done.emit(tr("状态读取失败: {err}", err=e))


# ---------- 关于页 ----------

class AboutPage(CardWidget):
    """关于面板: 版本信息 / 工作流程 / 模型 / 数据配置 / 致谢。"""
    # 更新检查结果(后台线程 -> 主线程): (显示文本, 状态 new/ok/err)
    _update_sig = Signal(str, str)
    # 下载进度(后台线程 -> 主线程): (已完成字节, 总字节)
    _dl_sig = Signal(int, int)
    # 下载结束: (成功?, 错误描述)
    _dl_done_sig = Signal(bool, str)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._state = "idle"      # idle/checking/ready/downloading/downloaded/installing
        self._asset = None        # 新版 zip 资产信息
        self._dl_path = None      # 下载目标路径
        self._cancel = None       # threading.Event, 取消下载
        self._auto_dl = False     # 横幅"更新"按钮: 检查到新版后自动开始下载
        self._update_sig.connect(self._on_update_result)
        self._dl_sig.connect(self._on_dl_progress)
        self._dl_done_sig.connect(self._on_dl_done)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.setSpacing(12)

        # 顶部横幅 header(logo_big 宽图)
        banner = CardWidget()
        vb = QVBoxLayout(banner)
        vb.setContentsMargins(0, 0, 0, 0)
        banner_lbl = QLabel()
        banner_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        banner_pix = QPixmap(APP_BANNER) if os.path.exists(APP_BANNER) else QPixmap()
        if not banner_pix.isNull():
            banner_lbl.setPixmap(banner_pix.scaledToWidth(
                560, Qt.TransformationMode.SmoothTransformation))
        vb.addWidget(banner_lbl)
        outer.addWidget(banner)

        outer.addWidget(StrongBodyLabel(tr("关于")))

        grid = QGridLayout()
        grid.setSpacing(12)

        # 工作流程卡
        flow = CardWidget()
        vf = QVBoxLayout(flow)
        vf.setContentsMargins(16, 14, 16, 14)
        vf.setSpacing(8)
        vf.addWidget(self._title(FluentIcon.MESSAGE, tr("工作流程")))
        for st in [
            tr("1. 按住快捷键(默认左 Ctrl)说话, 松开即发送,"),
            tr("2. 如果OSC不可用，请切换至剪贴板模式"),
            tr("3. 剪贴板模式下，请确保VRChat始终在前台"),
            tr("4. 默认使用本地模型，在设置中可以切换"),
            tr("5. 识别不准? 靠近麦克风, 或换模型/开润色"),
            tr("6. 出问题看 vrcvoice.log"),
        ]:
            lbl = CaptionLabel(st)
            lbl.setWordWrap(True)
            vf.addWidget(lbl)
        vf.addStretch(1)
        grid.addWidget(flow, 0, 0)

        # 模型信息卡
        model = CardWidget()
        vm = QVBoxLayout(model)
        vm.setContentsMargins(16, 14, 16, 14)
        vm.setSpacing(8)
        vm.addWidget(self._title(FluentIcon.ROBOT, tr("模型")))
        for st in [
            tr("模型: sherpa-onnx (中英双语流式)"),
            tr("本地离线识别 / 云端不占空间"),
            tr("从模型库选择(在 AI 设置页添加)"),
        ]:
            lbl = CaptionLabel(st)
            lbl.setWordWrap(True)
            vm.addWidget(lbl)
        vm.addStretch(1)
        grid.addWidget(model, 0, 1)

        # 数据与配置卡
        data = CardWidget()
        vd = QVBoxLayout(data)
        vd.setContentsMargins(16, 14, 16, 14)
        vd.setSpacing(8)
        vd.addWidget(self._title(FluentIcon.DOCUMENT, tr("数据与配置")))
        for st in [
            tr("所有设置均存 config.json 可查可改"),
            tr("日志: vrcvoice.log, 出问题先看它"),
        ]:
            lbl = CaptionLabel(st)
            lbl.setWordWrap(True)
            vd.addWidget(lbl)
        row = QHBoxLayout()
        row.setSpacing(8)
        btn_cfg = PushButton(tr("打开配置文件"))
        btn_cfg.clicked.connect(self._open_config)
        btn_data = PushButton(tr("打开数据目录"))
        btn_data.clicked.connect(self._open_data_dir)
        row.addWidget(btn_cfg)
        row.addWidget(btn_data)
        row.addStretch(1)
        vd.addLayout(row)
        vd.addStretch(1)
        grid.addWidget(data, 1, 0)

        # 致谢卡
        thanks = CardWidget()
        vt = QVBoxLayout(thanks)
        vt.setContentsMargins(16, 14, 16, 14)
        vt.setSpacing(8)
        vt.addWidget(self._title(FluentIcon.HEART, tr("致谢")))

        # 版本 + 检查更新
        row_ver = QHBoxLayout()
        row_ver.setSpacing(8)
        ver_lbl = BodyLabel(tr("当前版本 v{ver}", ver=local_version()))
        ver_lbl.setWordWrap(True)
        row_ver.addWidget(ver_lbl)
        row_ver.addStretch(1)
        self._btn_check = PushButton(tr("检查更新"))
        self._btn_check.clicked.connect(self._on_check_clicked)
        row_ver.addWidget(self._btn_check)
        vt.addLayout(row_ver)
        self._update_lbl = CaptionLabel("")
        self._update_lbl.setWordWrap(True)
        vt.addWidget(self._update_lbl)
        # 下载进度条(仅下载中显示; range(0,0)=连接中不确定态)
        self._dl_bar = ProgressBar()
        self._dl_bar.setFixedHeight(6)
        self._dl_bar.setRange(0, 100)
        self._dl_bar.setValue(0)
        self._dl_bar.hide()
        vt.addWidget(self._dl_bar)

        for st in [
            tr("免费软件，感谢为这个软件做出过贡献或提出建议的所有人"),
            tr("使用中遇到问题或想提建议, 欢迎反馈"),
        ]:
            lbl = CaptionLabel(st)
            lbl.setWordWrap(True)
            vt.addWidget(lbl)
        row_home = QHBoxLayout()
        row_home.setSpacing(8)
        btn_home = PushButton(tr("项目首页"))
        btn_home.clicked.connect(self._open_home)
        row_home.addWidget(btn_home)
        row_home.addStretch(1)
        vt.addLayout(row_home)
        vt.addStretch(1)
        grid.addWidget(thanks, 1, 1)

        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        outer.addLayout(grid, 1)

    def _title(self, icon, text):
        row = QHBoxLayout()
        row.setSpacing(8)
        ic = IconWidget(icon)
        ic.setFixedSize(18, 18)
        row.addWidget(ic)
        row.addWidget(StrongBodyLabel(text))
        row.addStretch(1)
        box = QWidget()
        box.setLayout(row)
        return box

    def _open_config(self):
        import subprocess, os
        from ..log import data_dir
        p = os.path.join(data_dir(), "config.json")
        if not os.path.isfile(p):
            p = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config.json")
        if os.path.isfile(p):
            subprocess.Popen(["notepad", p])

    def _open_data_dir(self):
        import subprocess
        from ..log import data_dir
        subprocess.Popen(["explorer", data_dir()])

    def _open_home(self):
        """打开项目首页(GitHub 仓库)。"""
        QDesktopServices.openUrl(QUrl("https://github.com/Tianxiaorui9001/VRCVoice"))

    # ---------- 检查更新 / 下载 / 安装 ----------

    def _on_check_clicked(self):
        """按钮状态机: 检查更新 / 下载 / 取消下载 / 重启并安装。"""
        if self._state == "ready":
            self._start_download()
        elif self._state == "downloading":
            if self._cancel is not None:
                self._cancel.set()
        elif self._state == "downloaded":
            self._install_update()
        else:
            self._do_check_update()

    # --- 检查 ---

    def _do_check_update(self):
        if self._state in ("checking", "downloading"):
            return
        self._state = "checking"
        self._btn_check.setEnabled(False)
        self._update_lbl.setText(tr("检查中..."))
        self._update_lbl.setStyleSheet("color: #8a8a8a;")
        threading.Thread(target=self._check_worker, daemon=True).start()

    def _check_worker(self):
        force = bool(self.settings.get("debug", "force_check_update"))
        latest, _local, has_update, err, asset = check_latest(force=force)
        if err:
            self._update_sig.emit(tr("检查失败，请检查网络后重试"), "err")
        elif has_update:
            self._asset = asset
            self._update_sig.emit(tr("发现新版本 {ver}", ver=latest), "new")
        else:
            self._asset = None
            self._update_sig.emit(tr("已是最新版本 (v{ver})", ver=latest), "ok")

    def _on_update_result(self, text, kind):
        self._state = "ready" if kind == "new" else "idle"
        self._btn_check.setEnabled(True)
        self._dl_bar.hide()
        if kind == "new":
            self._btn_check.setText(tr("下载"))
            color = "#f59e0b"
        else:
            self._btn_check.setText(tr("检查更新"))
            color = "#ef4444" if kind == "err" else "#4ade80"
        self._update_lbl.setText(text)
        self._update_lbl.setStyleSheet(f"color: {color};")
        # 横幅"更新"按钮触发: 检查到新版后自动开始下载
        if kind == "new" and self._auto_dl:
            self._auto_dl = False
            self._start_download()

    def start_auto_download(self):
        """首页横幅"更新"按钮: 导航到关于页后自动下载新版。"""
        if self._state == "ready" and self._asset:
            self._start_download()
        elif self._state == "idle":
            self._auto_dl = True
            self._do_check_update()
        # checking/downloading/downloaded/installing: 不做额外动作
    # --- 下载 ---

    def _start_download(self):
        if not self._asset or not self._asset.get("url"):
            self._update_lbl.setText(tr("下载失败，请检查网络后重试"))
            self._update_lbl.setStyleSheet("color: #ef4444;")
            return
        self._state = "downloading"
        self._cancel = threading.Event()
        self._dl_path = os.path.join(update_dir(), self._asset["name"])
        self._btn_check.setText(tr("取消下载"))
        self._update_lbl.setText(tr("正在连接下载服务器..."))
        self._update_lbl.setStyleSheet("color: #8a8a8a;")
        # 不确定态(流动动画)直到收到第一块数据
        self._dl_bar.setRange(0, 0)
        self._dl_bar.show()
        threading.Thread(target=self._dl_worker, daemon=True).start()

    def _dl_worker(self):
        ok, err = download_release(self._asset, self._dl_path,
                                   progress=self._dl_sig.emit,
                                   cancel=self._cancel)
        self._dl_done_sig.emit(ok, err)

    def _on_dl_progress(self, done, total):
        if self._state != "downloading":
            return
        # 第一块数据到达: 切确定进度
        if self._dl_bar.maximum() == 0 and total > 0:
            self._dl_bar.setRange(0, 100)
        pct = done * 100 // total if total > 0 else 0
        self._dl_bar.setValue(min(100, pct))
        text = tr("正在下载 {pct}% ({done}/{total} MB)",
                  pct=pct, done=round(done / 1048576, 1), total=round(total / 1048576, 1))
        self._update_lbl.setText(text)

    def _on_dl_done(self, ok, err):
        if self._state != "downloading":
            return
        if ok:
            self._state = "downloaded"
            size = round(os.path.getsize(self._dl_path) / 1048576, 1)
            self._btn_check.setText(tr("重启并安装"))
            self._update_lbl.setText(tr("下载完成 ({size} MB)", size=size))
            self._update_lbl.setStyleSheet("color: #4ade80;")
            self._dl_bar.setRange(0, 100)
            self._dl_bar.setValue(100)
        elif err == "cancelled":
            self._state = "ready"
            self._btn_check.setText(tr("下载"))
            self._update_lbl.setText(tr("已取消下载"))
            self._update_lbl.setStyleSheet("color: #8a8a8a;")
            self._dl_bar.hide()
        elif err == "sha256-mismatch":
            self._state = "ready"
            self._btn_check.setText(tr("下载"))
            self._update_lbl.setText(tr("下载校验失败，请重试"))
            self._update_lbl.setStyleSheet("color: #ef4444;")
            self._dl_bar.hide()
        else:
            self._state = "ready"
            self._btn_check.setText(tr("下载"))
            self._update_lbl.setText(tr("下载失败，请检查网络后重试"))
            self._update_lbl.setStyleSheet("color: #ef4444;")
            self._dl_bar.hide()

    # --- 安装(重启替换) ---

    def _install_update(self):
        """写 updater.bat(路径写死+每步 trace) -> 启动独立进程 -> 退出主程序,
        由 bat 替换文件并拉起新版。"""
        import subprocess
        if not self._dl_path or not os.path.isfile(self._dl_path):
            self._update_lbl.setText(tr("下载失败，请检查网络后重试"))
            self._update_lbl.setStyleSheet("color: #ef4444;")
            return
        self._state = "installing"
        self._btn_check.setEnabled(False)
        self._update_lbl.setText(tr("正在安装，请稍候..."))
        self._update_lbl.setStyleSheet("color: #f59e0b;")
        updir = update_dir()
        bat = os.path.join(updir, "updater.bat")
        tmp = os.path.join(updir, "extract")
        trace = os.path.join(updir, "install_trace.log")
        if getattr(sys, "frozen", False):
            install_dir = os.path.dirname(sys.executable)
        else:
            install_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        try:
            try:
                os.remove(trace)
            except OSError:
                pass
            try:
                os.remove(bat)
            except OSError:
                pass
            with open(bat, "w", encoding="ascii") as f:
                f.write(_UPDATER_BAT.format(zip=self._dl_path, dst=install_dir,
                                            tmp=tmp, trace=trace))
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen(["cmd", "/c", bat], cwd=updir, close_fds=True,
                             creationflags=flags)
            # 给 cmd 一点启动时间, 再退出主程序(安装场景无需优雅清理)
            time.sleep(0.5)
        except Exception as e:
            log(f"[update] 安装启动失败: {e}")
            try:
                with open(trace, "a", encoding="ascii") as f:
                    f.write(f"[POPEN_FAIL] {e}\n")
            except Exception:
                pass
            self._state = "downloaded"
            self._btn_check.setEnabled(True)
            self._update_lbl.setText(tr("下载失败，请检查网络后重试"))
            self._update_lbl.setStyleSheet("color: #ef4444;")
            return
        # 退出事件循环后立即终止进程: 安装场景无需优雅清理(热键/VR 等),
        # 直接让 updater.bat 接管, 避免旧进程残留导致新版被单实例锁挡下
        app = QApplication.instance()
        if app is not None:
            app.quit()
        os._exit(0)


# ---------- 状态页 ----------

class StatusPage(CardWidget):
    model_status = Signal(str)  # 模型状态文本(工作线程 -> GUI 线程安全更新)
    _state_sig = Signal(bool)    # 录音状态回调桥(controller 后台线程 -> 主线程)
    _partial_sig = Signal(str)   # 实时识别回调桥
    _finished_sig = Signal(str)  # 发送完成回调桥

    def __init__(self, controller: RecognitionController, settings: Settings, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.settings = settings
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(24, 24, 24, 24)
        self._outer.setSpacing(12)
        # 更新横幅(发现新版本时显示): 轻量提醒, 不弹窗不打断
        self._banner = CardWidget()
        self._banner.setVisible(False)
        hb = QHBoxLayout(self._banner)
        hb.setContentsMargins(16, 10, 16, 10)
        hb.setSpacing(10)
        ic = IconWidget(FluentIcon.UPDATE)
        ic.setFixedSize(20, 20)
        hb.addWidget(ic)
        self._banner_lbl = BodyLabel("")
        self._banner_lbl.setWordWrap(True)
        hb.addWidget(self._banner_lbl, 1)
        self._banner_btn = PrimaryPushButton(tr("更新"))
        self._banner_btn.clicked.connect(self._banner_update_clicked)
        hb.addWidget(self._banner_btn)
        self._banner.setStyleSheet(
            "CardWidget { background: rgba(245, 158, 11, 0.10);"
            " border: 1px solid rgba(245, 158, 11, 0.45); border-radius: 8px; }")
        self._on_banner_update = None
        self._outer.addWidget(self._banner)
        self._outer.addWidget(StrongBodyLabel(tr("主页")))

        grid = QGridLayout()
        grid.setSpacing(12)

        # --- 模块 1: 连接状态 ---
        card_conn = CardWidget()
        v1 = QVBoxLayout(card_conn)
        v1.setContentsMargins(16, 14, 16, 14)
        v1.setSpacing(8)
        v1.addWidget(self._card_title(FluentIcon.LINK, tr("连接状态")))
        self.vrc_status_label = BodyLabel("VRChat: ")
        self._update_vrc_status()  # 先查一次
        self._vrc_timer = QTimer(self)
        self._vrc_timer.timeout.connect(self._update_vrc_status)
        self._vrc_timer.start(5000)
        v1.addWidget(self.vrc_status_label)
        self.model_label = CaptionLabel(tr("模型: 未加载"))
        v1.addWidget(self.model_label)
        self.model_status.connect(self.model_label.setText)
        v1.addStretch(1)

        # --- 模块 2: 录音控制 ---
        card_rec = CardWidget()
        v2 = QVBoxLayout(card_rec)
        v2.setContentsMargins(16, 14, 16, 14)
        v2.setSpacing(8)
        v2.addWidget(self._card_title(FluentIcon.MICROPHONE, tr("录音控制")))
        self.status_label = BodyLabel(tr("录音: 空闲"))
        self.status_label.setStyleSheet("font-size: 20px; font-weight: 600;")
        v2.addWidget(self.status_label)
        self.partial_label = CaptionLabel(tr("实时识别: -"))
        self.partial_label.setWordWrap(True)
        v2.addWidget(self.partial_label)
        self.test_btn = PrimaryPushButton(tr("按住说话"))
        self.test_btn.setMinimumHeight(44)
        self.test_btn.pressed.connect(self._start)
        self.test_btn.released.connect(self._stop)
        v2.addWidget(self.test_btn)

        # --- 模块 3: 最近发送 ---
        card_last = CardWidget()
        v3 = QVBoxLayout(card_last)
        v3.setContentsMargins(16, 14, 16, 14)
        v3.setSpacing(8)
        v3.addWidget(self._card_title(FluentIcon.SEND, tr("最近发送")))
        self.last_label = BodyLabel(tr("最近发送: -"))
        self.last_label.setWordWrap(True)
        v3.addWidget(self.last_label, 1)
        last_row = QHBoxLayout()
        self.copy_btn = PushButton(tr("复制"), self)
        self.copy_btn.clicked.connect(self._copy_last)
        last_row.addWidget(self.copy_btn)
        v3.addLayout(last_row)

        grid.addWidget(card_conn, 0, 0)
        grid.addWidget(card_rec, 0, 1)
        grid.addWidget(card_last, 1, 0, 1, 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self._outer.addLayout(grid, 1)

        # --- 使用说明(全宽单卡) ---
        tips_card = CardWidget()
        vt = QVBoxLayout(tips_card)
        vt.setContentsMargins(16, 14, 16, 14)
        vt.setSpacing(4)
        vt.addWidget(self._card_title(FluentIcon.INFO, tr("使用说明")))
        tips = [
            tr("1. 按住快捷键(默认左 Ctrl)说话, 松开即发送,"),
            tr("2. OSC 需要在 VRChat 动作菜单(ESC) → Osc → Enabled 打开,"),
            tr("3. OSC 显示头顶气泡; 剪贴板模式复制到剪贴板手动粘贴,"),
            tr("4. 空间紧张? 识别后端切云端, 本地模型可删"),
            tr("5. 识别不准? 靠近麦克风, 或换模型/开润色"),
            tr("6. 出问题看 vrcvoice.log"),
        ]
        for t in tips:
            lbl = CaptionLabel(t)
            lbl.setWordWrap(True)
            vt.addWidget(lbl)
        self._outer.addWidget(tips_card)

        self._state_sig.connect(self._on_state)
        self._partial_sig.connect(self._on_partial)
        self._finished_sig.connect(self._on_finished)
        # controller 回调可能来自录音/润色后台线程, 必须桥回主线程再碰 QWidget
        self.controller.on_state_changed = self._state_sig.emit
        self.controller.on_partial = self._partial_sig.emit
        self.controller.on_finished = self._finished_sig.emit

        backend = self.controller.backend_name
        self.model_label.setText(tr("模型: 加载中..."))
        self._init_model(backend)

    def _card_title(self, icon: FluentIcon, text: str) -> QWidget:
        row = QHBoxLayout()
        row.setSpacing(8)
        ic = IconWidget(icon)
        ic.setFixedSize(18, 18)
        row.addWidget(ic)
        row.addWidget(StrongBodyLabel(text))
        row.addStretch(1)
        box = QWidget()
        box.setLayout(row)
        return box

    # --- 更新横幅(启动检查发现新版本时显示) ---

    def show_update_banner(self, ver: str, on_update):
        """显示顶部更新提醒横幅。on_update: 点击"更新"后的回调。"""
        self._banner_lbl.setText(tr("发现新版本 v{ver}，点击更新即可一键升级", ver=ver))
        self._on_banner_update = on_update
        self._banner.setVisible(True)

    def hide_update_banner(self):
        self._banner.setVisible(False)

    def _banner_update_clicked(self):
        if self._on_banner_update is not None:
            cb = self._on_banner_update
            self._on_banner_update = None
            cb()


    def _init_model(self, backend: str):
        """异步加载模型, 失败写日志, 不阻塞界面。"""
        from ..log import log

        def _load():
            try:
                self.controller.init_asr()
                if backend == "cloud":
                    self.model_status.emit(tr("后端: 云端 ({model})", model=self.settings.get("recognition", "cloud_model")))
                else:
                    self.model_status.emit("后端: 本地模型 已加载 ✓")
            except Exception as e:
                log(f"[asr] 模型加载失败: {e}")
                self.model_status.emit(tr("本地模型未加载({err}) - 点'重新加载模型'重试, 或看 vrcvoice.log", err=e))

        threading.Thread(target=_load, daemon=True).start()

    def _reload_model(self):
        from ..log import log
        self.model_status.emit(tr("模型: 重新加载中..."))

        def work():
            ok, err = self.controller.reload_asr()
            backend = self.controller.backend_name
            if ok:
                if backend == "cloud":
                    self.model_status.emit(tr("后端: 云端 ({model})", model=self.settings.get("recognition", "cloud_model")))
                else:
                    self.model_status.emit("后端: 本地模型 已加载 ✓")
            else:
                log(f"[asr] 重新加载失败: {err}")
                self.model_status.emit(tr("本地模型未加载({err}) - 可切换云端后端或看 vrcvoice.log", err=err))

        threading.Thread(target=work, daemon=True).start()

    def refresh_mic(self):
        from ..recorder import Recorder
        devs = Recorder.list_devices()
        InfoBar.success(tr("麦克风列表"), ", ".join(d[0] for d in devs[:5]) + (tr("等") if len(devs) > 5 else ""),
                        parent=self, position=InfoBarPosition.TOP)

    def _copy_last(self):
        from PySide6.QtWidgets import QApplication
        text = getattr(self.controller, "_last_text", "") or ""
        if text:
            QApplication.clipboard().setText(text)
            InfoBar.success(tr("已复制"), text[:30], parent=self,
                            position=InfoBarPosition.TOP, duration=2000)

    def _on_state(self, recording: bool):
        self.status_label.setText(tr("录音: 录音中…") if recording else tr("录音: 空闲"))
        self.status_label.setStyleSheet(
            "font-size: 20px; font-weight: 600; color: #e5484d;" if recording
            else "font-size: 20px; font-weight: 600;")

    def _on_partial(self, text: str):
        self.partial_label.setText(tr("实时识别: {text}", text=text))

    def _on_finished(self, text: str):
        if text:
            self.last_label.setText(tr("最近发送: {text}", text=text))
        elif text.startswith("[错误]"):
            self.last_label.setText(tr("错误: {text}", text=text))
        else:
            self.last_label.setText(tr("最近发送: (未识别到内容)"))

    def _update_vrc_status(self):
        from ..vrc_status import vrc_status
        status = vrc_status()
        # 运行中(VR/桌面)统一绿色, 未运行灰色 —— 避免橙/绿双色造成困惑
        if status == "未运行":
            style = "color: #8a8a8a;"
        else:
            style = "color: #2ea121;"
        self.vrc_status_label.setStyleSheet(f"font-size: 15px; {style}")
        self.vrc_status_label.setText(tr("VRChat: {status}", status=tr(status)))

    def _osc_test(self):
        msg = self.controller.output.osc_test()
        InfoBar.success(tr("OSC 测试"), msg, parent=self,
                        position=InfoBarPosition.TOP, duration=6000)

    def _start(self):
        self.controller.start()

    def _stop(self):
        self.controller.stop()


# ---------- 主窗口 ----------

class _ModelEditDialog(QDialog):
    """entry"""
    _fetch_done = Signal(list)
    _fetch_error = Signal(str)
    _test_done = Signal(str, bool)

    _PROVIDER_ITEMS = [("custom", tr("自定义")), ("deepseek", "DeepSeek"),
                       ("siliconflow", tr("硅基流动")), ("openai", "OpenAI (ChatGPT)"),
                       ("anthropic", "Claude"), ("gemini", "Gemini")]

    def __init__(self, settings, controller, entry=None, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.controller = controller
        self.entry = entry          # None=新建; dict=编辑(引用模型库里的条目)
        self.result_entry = None
        self.deleted = False
        self.setWindowTitle(tr("添加模型") if entry is None else tr("编辑模型"))
        self.setMinimumWidth(620)

        self._fetch_done.connect(self._on_fetch_done)
        self._fetch_error.connect(self._on_fetch_error)
        self._test_done.connect(self._on_test_done)

        form = QFormLayout(self)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(12)

        self.name_edit = LineEdit(self)
        self.name_edit.setPlaceholderText("DeepSeek")
        form.addRow(tr("名称 *"), self.name_edit)

        self.kind_combo = ComboBox(self)
        self.kind_combo.addItems([tr("云端服务"), tr("本地模型")])
        form.addRow(tr("类型"), self.kind_combo)

        self.provider_combo = ComboBox(self)
        for raw, disp in self._PROVIDER_ITEMS:
            self.provider_combo.addItem(disp, userData=raw)
        form.addRow(tr("服务商"), self.provider_combo)

        self.endpoint_edit = LineEdit(self)
        self.endpoint_edit.setPlaceholderText("https://api.xxx.com/v1/chat/completions")
        form.addRow(tr("API 地址 *"), self.endpoint_edit)

        self.key_edit = LineEdit(self)
        self.key_edit.setPlaceholderText(tr("sk-... (本地模型一般留空)"))
        self.key_edit.setEchoMode(LineEdit.EchoMode.Password)
        form.addRow("API Key", self.key_edit)

        model_row = QHBoxLayout()
        self.model_edit = LineEdit(self)
        self.model_edit.setPlaceholderText(tr("模型名, 如 deepseek-chat / qwen2.5:7b"))
        model_row.addWidget(self.model_edit, 1)
        self.fetch_btn = PushButton(tr("获取模型"), self)
        self.fetch_btn.clicked.connect(self._fetch)
        model_row.addWidget(self.fetch_btn)
        form.addRow(tr("模型 *"), model_row)

        self.timeout_spin = QSpinBox(self)
        self.timeout_spin.setRange(5, 120)
        self.timeout_spin.setValue(15)
        self.timeout_spin.setSuffix(tr(" 秒"))
        form.addRow(tr("超时"), self.timeout_spin)

        btns = QHBoxLayout()
        self.test_btn = PushButton(tr("测试连通"), self)
        self.test_btn.clicked.connect(self._test)
        btns.addWidget(self.test_btn)
        self.del_btn = PushButton(tr("删除"), self)
        self.del_btn.clicked.connect(self._delete)
        btns.addWidget(self.del_btn)
        btns.addStretch(1)
        cancel_btn = PushButton(tr("取消"), self)
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(cancel_btn)
        self.save_btn = PrimaryPushButton(tr("保存"), self)
        self.save_btn.clicked.connect(self._save)
        btns.addWidget(self.save_btn)
        form.addRow(btns)

        # 预填
        if entry:
            self.name_edit.setText(entry.get("name", ""))
            self.kind_combo.setCurrentText(tr("本地模型") if entry.get("kind") == "local" else tr("云端服务"))
            idx = self.provider_combo.findData(entry.get("provider", "custom"))
            if idx >= 0:
                self.provider_combo.setCurrentIndex(idx)
            self.endpoint_edit.setText(entry.get("endpoint", ""))
            self.key_edit.setText(entry.get("api_key", ""))
            self.model_edit.setText(entry.get("model", ""))
            self.timeout_spin.setValue(int(entry.get("timeout_sec", 15) or 15))
        self.kind_combo.currentIndexChanged.connect(self._on_kind_changed)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        self._on_kind_changed(self.kind_combo.currentIndex())

    def _set_row_visible(self, form: QFormLayout, widget, visible: bool):
        try:
            form.setRowVisible(widget, visible)
        except Exception:
            widget.setVisible(visible)

    def _on_kind_changed(self, idx):
        is_local = idx == 1
        self._set_row_visible(self.layout(), self.provider_combo, not is_local)
        self.fetch_btn.setVisible(not is_local)
        if is_local:
            self.endpoint_edit.setPlaceholderText("http://127.0.0.1:11434/v1/chat/completions")
            self.key_edit.setPlaceholderText(tr("本地服务一般不用 Key, 留空即可"))
            self.endpoint_edit.setText("http://127.0.0.1:11434/v1/chat/completions")
        else:
            self.endpoint_edit.setPlaceholderText("https://api.xxx.com/v1/chat/completions")
            self.key_edit.setPlaceholderText(tr("sk-... (本地模型一般留空)"))

    def _on_provider_changed(self, idx):
        raw = self.provider_combo.currentData() or "custom"
        p = PROVIDERS.get(raw)
        if p and self.kind_combo.currentIndex() == 0:
            self.endpoint_edit.setText(p["endpoint"])

    def _collect(self):
        return {
            "name": self.name_edit.text().strip(),
            "kind": "local" if self.kind_combo.currentIndex() == 1 else "cloud",
            "provider": self.provider_combo.currentData() or "custom",
            "endpoint": self.endpoint_edit.text().strip(),
            "api_key": self.key_edit.text().strip(),
            "model": self.model_edit.text().strip(),
            "timeout_sec": self.timeout_spin.value(),
        }

    def _save(self):
        data = self._collect()
        if not data["name"]:
            InfoBar.warning(tr("请填名称"), "", parent=self)
            return
        if not data["model"]:
            InfoBar.warning(tr("请填模型名"), "", parent=self)
            return
        if not data["endpoint"]:
            InfoBar.warning(tr("请填 API 地址"), "", parent=self)
            return
        for m in self.settings.ai_models():
            if m.get("name") == data["name"] and m is not self.entry:
                InfoBar.warning(tr("已有同名模型 {name}", name=repr(data["name"])), tr("换个名字"), parent=self)
                return
        self.result_entry = data
        self.accept()

    def _delete(self):
        if not self.entry:
            return
        ret = QMessageBox.question(
            self, tr("删除模型"),
            tr("确定删除模型 {name}?\n润色/翻译若正在用它, 选择会被清空", name=repr(self.entry.get("name"))))
        if ret == QMessageBox.StandardButton.Yes:
            self.deleted = True
            self.accept()

    def _fetch(self):
        raw = self.provider_combo.currentData() or "custom"
        p = PROVIDERS.get(raw)
        if not p:
            InfoBar.warning(tr("自定义服务商无预设地址"), tr("请手动填写模型名"), parent=self)
            return
        key = self.key_edit.text().strip()
        if not key:
            InfoBar.warning("API Key", "", parent=self)
            return
        self.fetch_btn.setEnabled(False)
        self.fetch_btn.setText(tr("获取中..."))
        threading.Thread(target=self._fetch_bg, args=(p, key), daemon=True).start()

    def _fetch_bg(self, p: dict, key: str):
        try:
            models = self.controller.fetch_models("", "", key, p["models_url"], p["kind"])
            self._fetch_done.emit(list(models))
        except Exception as e:
            self._fetch_error.emit(str(e))

    def _on_fetch_done(self, models: list):
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText(tr("获取模型"))
        if not models:
            InfoBar.warning(tr("该服务商没有可用模型"), "", parent=self)
            return
        item, ok = QInputDialog.getItem(self, tr("选择模型"), tr("选一个填入:"),
                                        models, 0, False)
        if ok and item:
            self.model_edit.setText(item)
            InfoBar.success(tr("已填入 {item}", item=item), "", parent=self)

    def _on_fetch_error(self, err: str):
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText(tr("获取模型"))
        InfoBar.error(tr("获取模型失败"), err[:120], parent=self, duration=8000)

    def _test(self):
        data = self._collect()
        if not data["model"] or not data["endpoint"]:
            InfoBar.warning(tr("先填模型名和 API 地址"), "", parent=self)
            return
        self.test_btn.setEnabled(False)
        self.test_btn.setText(tr("测试中..."))
        threading.Thread(target=self._test_bg, args=(data,), daemon=True).start()

    def _test_bg(self, data: dict):
        try:
            r = self.controller._chat_completion(
                data["provider"], data["model"], data["endpoint"], data["api_key"],
                data["timeout_sec"], "你是一个连通性测试助手。", "请只回复两个字: 收到")
            self._test_done.emit((r or "")[:80], True)
        except Exception as e:
            self._test_done.emit(str(e)[:160], False)

    def _on_test_done(self, text: str, ok: bool):
        self.test_btn.setEnabled(True)
        self.test_btn.setText(tr("测试连通"))
        if ok:
            InfoBar.success(tr("测试成功"), text, parent=self, duration=5000)
        else:
            InfoBar.error(tr("测试失败"), text, parent=self, duration=8000)


class MainWindow(FluentWindow):
    _test_done = Signal(list)  # 一键测试结果 [(idx, ok, msg)] 后台线程 -> 主线程
    _tr_test_done = Signal(str, str)  # 翻译测试结果 (state, text) 后台线程 -> 主线程
    _banner_sig = Signal(str)   # 启动检查发现新版本(后台线程 -> 主线程)

    def __init__(self, settings: Settings, controller: RecognitionController,
                 on_hotkey_changed=None):
        super().__init__()
        self.settings = settings
        self.controller = controller
        self._on_hotkey_changed = on_hotkey_changed
        self.setWindowTitle(tr("VRCVoice - VRChat 语音输入助手"))
        self.resize(980, 720)
        self.tray_visible = False

        # 状态页: SmoothScrollArea 包裹卡片, 避免 FluentWindow 下布局不激活
        self.status_card = StatusPage(controller, settings, self)
        self.status_page = SmoothScrollArea()
        self.status_page.setWidgetResizable(True)
        self.status_page.setWidget(self.status_card)
        self._transparent_scroll(self.status_page)

        self.settings_page = SmoothScrollArea()
        self.settings_page.setWidgetResizable(True)
        self._build_settings_page()
        self._transparent_scroll(self.settings_page)

        self.polish_page = SmoothScrollArea()
        self.polish_page.setWidgetResizable(True)
        self._build_polish_page()
        self._transparent_scroll(self.polish_page)

        self.translate_page = SmoothScrollArea()
        self.translate_page.setWidgetResizable(True)
        self._build_translate_page()
        self._transparent_scroll(self.translate_page)

        self.ai_models_page = SmoothScrollArea()
        self.ai_models_page.setWidgetResizable(True)
        self._build_ai_models_page()
        self._transparent_scroll(self.ai_models_page)

        self.log_page = LogPage()

        self.about_page = SmoothScrollArea()
        self.about_page.setWidgetResizable(True)
        self.about_page.setWidget(AboutPage(settings, self))
        self._transparent_scroll(self.about_page)

        self.status_page.setObjectName("statusPage")
        self.settings_page.setObjectName("settingsPage")
        self.polish_page.setObjectName("polishPage")
        self.translate_page.setObjectName("translatePage")
        self.ai_models_page.setObjectName("aiModelsPage")
        self.log_page.setObjectName("logPage")

        # 对话记录页: 信号发自触发/润色线程, UI 必须主线程
        from .chat_log_page import ChatLogPage
        from PySide6.QtCore import QObject, Signal as QSignal

        class _LogBridge(QObject):
            entry = QSignal(dict)

        self._log_bridge = _LogBridge()
        self._log_bridge.entry.connect(self._append_chat_log)
        self.chat_log_page = ChatLogPage(self)
        self.chat_log_page.setObjectName("chatLogPage")
        self.controller.on_message_logged = self._on_message_logged

        self.addSubInterface(self.status_page, FluentIcon.HOME, tr("状态"))
        self.addSubInterface(self.chat_log_page, FluentIcon.HISTORY, tr("对话记录"))
        self.addSubInterface(self.polish_page, FluentIcon.EDIT, tr("AI 润色"))
        self.addSubInterface(self.translate_page, FluentIcon.LANGUAGE, tr("AI 翻译"))
        self.addSubInterface(self.ai_models_page, FluentIcon.ROBOT, tr("AI 设置"),
                             NavigationItemPosition.BOTTOM)
        self.addSubInterface(self.settings_page, FluentIcon.SETTING, tr("设置"),
                             NavigationItemPosition.BOTTOM)
        self.addSubInterface(self.log_page, FluentIcon.HISTORY, tr("日志"),
                             NavigationItemPosition.BOTTOM)
        self.about_page.setObjectName("aboutPage")
        self.addSubInterface(self.about_page, FluentIcon.INFO, tr("关于"),
                             NavigationItemPosition.BOTTOM)

        # 启动检查更新(自动更新设置开启时): 延迟等窗口就绪, 有新版本 -> 首页横幅
        self._banner_sig.connect(self._on_banner)
        QTimer.singleShot(2500, self._startup_update_check)

    def _startup_update_check(self):
        """启动后台检查更新: 有新版且自动更新开启 -> 首页顶部横幅提醒。"""
        try:
            if not self.settings.get("general", "auto_update"):
                return
        except Exception:
            return
        threading.Thread(target=self._startup_update_worker, daemon=True).start()

    def _startup_update_worker(self):
        try:
            latest, _local, has_update, err, _asset = check_latest()
        except Exception:
            return
        if not err and has_update and latest:
            self._banner_sig.emit(latest)

    def _on_banner(self, ver):
        """主线程: 显示首页更新横幅。"""
        self.status_card.show_update_banner(ver, self._go_update)

    def _go_update(self):
        """横幅"更新"按钮: 隐藏横幅 -> 导航到关于页 -> 自动开始下载。"""
        self.status_card.hide_update_banner()
        # FluentWindow.switchTo 才是真正切页(setCurrentItem 只改选中高亮)
        try:
            self.switchTo(self.about_page)
        except Exception as e:
            print(f"[update] switchTo 失败: {e}")
            sw = getattr(self, "stackedWidget", None)
            if sw is not None:
                sw.setCurrentWidget(self.about_page)
        ap = self.about_page.widget()
        try:
            ap.start_auto_download()
        except Exception as e:
            print(f"[update] start_auto_download 失败: {e}")

    def _transparent_scroll(self, scroll):
        scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}"
            "QScrollArea > QWidget > QWidget{background:transparent;}")
        scroll.viewport().setAutoFillBackground(False)

    def _on_message_logged(self, entry: dict):
        """发送记录回调: 可能在触发线程/润色线程调用, 只 emit 信号桥。"""
        try:
            self._log_bridge.entry.emit(entry)
        except Exception:
            pass

    def _append_chat_log(self, entry: dict):
        """对话记录"""
        self.chat_log_page.append(entry)

    def _reload_model(self):
        self.status_card._reload_model()

    def _open_data_dir(self):
        import subprocess
        from ..log import data_dir
        try:
            subprocess.Popen(["explorer", data_dir()])
        except Exception as e:
            InfoBar.error(tr("打开失败"), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)

    def _open_models_dir(self):
        import subprocess
        from ..log import app_base_dir
        d = os.path.join(app_base_dir(), "models")
        if not os.path.isdir(d):
            InfoBar.warning(tr("目录不存在"), tr("models 文件夹还没创建"),
                            position=InfoBarPosition.TOP, duration=4000)
            return
        try:
            subprocess.Popen(["explorer", d])
        except Exception as e:
            InfoBar.error(tr("打开失败"), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)

    def _debug_overlay_show(self):
        from ..vr_overlay import get_overlay
        ov = get_overlay()
        if ov is None or not ov.ok:
            InfoBar.warning(tr("悬浮窗"), tr("未创建(SteamVR 未运行或创建失败), 看日志确认"),
                            position=InfoBarPosition.TOP, duration=4000)
            return
        ov.show(tr("调试显示"))
        st = ov.status()
        InfoBar.success(tr("已强制显示"), f"visible={st.get('visible')}", parent=self,
                        position=InfoBarPosition.TOP, duration=4000)

    def _debug_overlay_hide(self):
        from ..vr_overlay import get_overlay
        ov = get_overlay()
        if ov is None:
            return
        ov.hide()
        InfoBar.success(tr("已强制隐藏"), "", parent=self,
                        position=InfoBarPosition.TOP, duration=2000)

    def _debug_open_config(self):
        import subprocess
        from ..settings import CONFIG_PATH
        if os.path.exists(CONFIG_PATH):
            try:
                subprocess.Popen(["notepad", CONFIG_PATH])
            except Exception as e:
                InfoBar.error(tr("打开失败"), parent=self,
                              position=InfoBarPosition.TOP, duration=4000)
        else:
            InfoBar.warning(tr("配置文件"), tr("config.json 不存在, 重启后自动生成"),
                            position=InfoBarPosition.TOP, duration=4000)

    def _debug_env_info(self):
        import sys
        from PySide6.QtWidgets import QMessageBox
        from ..log import data_dir, app_base_dir, LOG_PATH
        lines = [
            tr("运行模式: {mode}", mode=tr("打包 exe") if getattr(sys, "frozen", False) else tr("源码")),
            f"Python: {sys.version.split()[0]}",
            tr("程序目录: {path}", path=app_base_dir()),
            tr("数据目录: {path}", path=data_dir()),
            tr("日志: {path}", path=LOG_PATH),
        ]
        QMessageBox.information(self, tr("环境信息"), "\n".join(lines))

    def _debug_reset_config(self):
        from PySide6.QtWidgets import QMessageBox
        ret = QMessageBox.question(
            self, tr("重置配置"),
            tr("恢复所有设置到默认值?\n(日志和模型文件不受影响)\n重置后设置页/润色页立即刷新为默认值"))
        if ret != QMessageBox.StandardButton.Yes:
            return
        # 刷新整个 config: 重建所有配置页(所有卡片重新从默认值读)
        self.settings.reset()
        # 刷新整个 config: 重建所有配置页(所有卡片重新从默认值读)
        self._build_settings_page()
        self._build_polish_page()
        self._build_translate_page()
        self._build_ai_models_page()
        try:
            self.controller.refresh()
        except Exception as e:
            print(f"[main] 重置后刷新运行时失败(重启后生效): {e}")
        InfoBar.success(tr("已重置"), tr("所有设置已恢复默认, 页面已刷新; 热键等运行项建议重启后完全生效"),
                        parent=self, position=InfoBarPosition.TOP, duration=5000)


    def _osc_test(self):
        msg = self.controller.output.osc_test()
        InfoBar.success(tr("OSC 测试"), msg, parent=self,
                        position=InfoBarPosition.TOP, duration=6000)

    def _build_settings_page(self):
        s = self.settings
        scroll_widget = QWidget()
        self.settings_page.setWidget(scroll_widget)
        scroll_widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(scroll_widget)
        layout.setContentsMargins(24, 12, 24, 16)
        layout.setSpacing(10)

        # ---- 识别 ----
        # 云端识别模型库警告横幅(无模型时显示, 与润色/翻译页风格一致)
        rec_banner = self._make_model_banner(self.settings_page)
        rec_banner._mb_title.setText(tr("没有配置模型"))
        rec_banner._mb_sub.setText(tr("云端识别需要先在「AI 设置」页添加模型(云端/本地都可以), 添加后回来就能用"))
        self.rec_banner = rec_banner
        layout.addWidget(rec_banner)
        grp = VRCSettingCardGroup(tr("识别设置"), self.settings_page)
        backend_card = ComboCard(
            tr("识别后端"), tr("本地离线识别 / 云端不占空间"),
            [tr("本地模型"), tr("云端识别")],
            lambda: s.get("recognition", "backend"),
            lambda v: s.set("recognition", "backend", v),
            value_map={"local": tr("本地模型"), "cloud": tr("云端识别")},
            icon=FluentIcon.ROBOT)
        grp.addSettingCard(backend_card)
        grp.addSettingCard(ComboCard(
            tr("识别语言"), "",
            [tr("中英双语"), tr("中文"), tr("英文")],
            lambda: s.get("general", "language"),
            lambda v: s.set("general", "language", v),
            value_map={"zh-en": tr("中英双语"), "zh": tr("中文"), "en": tr("英文")},
            icon=FluentIcon.LANGUAGE))

        model_card = LineCard(
            tr("模型目录"), tr("默认 models/ 文件夹"),
            lambda: s.default_model_dir(), lambda v: s.set("recognition", "model_dir", v),
            icon=FluentIcon.FOLDER)
        def _browse_model():
            d = QFileDialog.getExistingDirectory(self.settings_page, tr("选择模型目录"), s.default_model_dir())
            if d:
                s.set("recognition", "model_dir", d)
                model_card.edit.setText(d)
        model_card.set_after_change(lambda: None)

        def _open_hotwords():
            from .hotwords_dialog import HotwordsDialog
            dlg = HotwordsDialog(self.settings_page)

            def _on_saved():
                if self.controller.reload_asr_async():
                    InfoBar.success(tr("已保存"), tr("识别热词已生效"), parent=self,
                                    position=InfoBarPosition.TOP, duration=3000)
                else:
                    InfoBar.info(tr("已保存"), tr("正在识别中, 下次识别时生效"), parent=self,
                                 position=InfoBarPosition.TOP, duration=3000)
            dlg.on_saved = _on_saved
            dlg.exec()
        local_cards = [
            model_card,
            ButtonCard(tr("浏览模型目录"), tr("改完重启生效"), tr("浏览…"), _browse_model, icon=FluentIcon.FOLDER_ADD),
            ButtonCard(tr("识别热词"), tr("专有名词偏置(人名/游戏名等), 提升识别准确率"), tr("编辑…"), _open_hotwords, icon=FluentIcon.DICTIONARY),
            NumberCard(tr("最长录音秒数"), tr("防误触上限"),
                       lambda: s.get("recognition", "max_duration_sec"),
                       lambda v: s.set("recognition", "max_duration_sec", v), 1, 120, 1,
                       icon=FluentIcon.STOP_WATCH),
        ]
        # 静音自动停止: 仅"按一下切换"模式需要(切换后忘按会一直录, 静音自动断);
        # 按住说话模式松手即停, 用不上, 隐藏
        silence_card = NumberCard(tr("静音自动停止(秒)"), tr("0=关, 静音自动结束"),
                       lambda: s.get("recognition", "silence_stop_sec"),
                       lambda v: s.set("recognition", "silence_stop_sec", v), 0, 10, 0.5,
                       icon=FluentIcon.MUTE)
        for card in local_cards:
            grp.addSettingCard(card)

        cloud_cards = [
            LineCard(tr("API 地址"), tr("OpenAI 兼容地址 /v1/audio/transcriptions 结尾"),
                     lambda: s.get("recognition", "cloud_endpoint"),
                     lambda v: s.set("recognition", "cloud_endpoint", v),
                     icon=FluentIcon.GLOBE),
            LineCard("API Key", tr("硅基流动等平台的 Key"),
                     lambda: s.get("recognition", "cloud_api_key"),
                     lambda v: s.set("recognition", "cloud_api_key", v), password=True,
                     icon=FluentIcon.CERTIFICATE),
        ]
        cloud_model_card = CloudModelComboCard(
            tr("云端模型"), tr("从模型库选择(在 AI 设置页添加)"),
            lambda v: s.set("recognition", "cloud_model", v),
            icon=FluentIcon.CLOUD)
        self.cloud_model_card = cloud_model_card
        cloud_cards = [
            cloud_model_card,
            LineCard(tr("语言提示(可空)"), tr("默认自动"),
                     lambda: s.get("recognition", "cloud_language"),
                     lambda v: s.set("recognition", "cloud_language", v),
                     icon=FluentIcon.DICTIONARY),
            NumberCard(tr("云端超时(秒)"), "",
                       lambda: s.get("recognition", "cloud_timeout_sec"),
                       lambda v: s.set("recognition", "cloud_timeout_sec", int(v)), 5, 120, 5,
                       icon=FluentIcon.STOP_WATCH),
        ]
        for card in cloud_cards:
            grp.addSettingCard(card)

        # 麦克风: 设备 + 刷新放一起, 本地/云端共用(识别总归要话筒)
        mic_card = ComboCard(
            tr("麦克风设备"), tr("VR 时可选手头显麦克风"),
            [tr("(默认)")], lambda: s.get("recognition", "mic_device") or tr("(默认)"),
            lambda v: s.set("recognition", "mic_device", "" if v == tr("(默认)") else v),
            icon=FluentIcon.MICROPHONE)
        mic_card.set_after_change(self._refresh_mic_items)
        self.mic_card = mic_card
        grp.addSettingCard(mic_card)
        # 刷新按钮与设备选择同一行(右侧); ButtonCard 单独一行占位, 改为并入 hBoxLayout
        mic_refresh_btn = PushButton(tr("刷新"))
        mic_refresh_btn.setFixedWidth(72)
        mic_refresh_btn.clicked.connect(self._refresh_mic_items)
        mic_card.hBoxLayout.addWidget(mic_refresh_btn, 0, Qt.AlignmentFlag.AlignRight)

        def _apply_backend(v):
            for card in local_cards:
                card.setVisible(v == "local")
            for card in cloud_cards:
                card.setVisible(v == "cloud")
            grp._force_relayout()
        backend_card.set_after_change(_apply_backend)
        _apply_backend(s.get("recognition", "backend"))
        layout.addWidget(grp)
        # 云端模型下拉初始填充(与模型库同步)
        self.cloud_model_card.set_models(s.ai_model_names(), s.get("recognition", "cloud_model"))
        self.rec_banner.setVisible(not bool(s.ai_model_names()))

        # ---- 触发 ----
        grp = VRCSettingCardGroup(tr("触发设置"), self.settings_page)
        def _apply_trigger_mode(v):
            s.set("trigger", "mode", v)
            self._hotkey_reload()
            toggle_only = v == "toggle"
            silence_card.setVisible(toggle_only)          # 静音自动停止仅切换模式需要
            silence_threshold_card.setVisible(toggle_only)  # 阈值卡跟随
        trigger_mode = ComboCard(
            tr("触发模式"), tr("按住: 按住说松开发 / 切换: 按一下开始再按一下结束; PC 快捷键与 VR 摇杆共用此模式"),
            [tr("按住说话"), tr("按一下切换")],
            lambda: s.get("trigger", "mode"),
            lambda v: _apply_trigger_mode(v),
            value_map={"hold": tr("按住说话"), "toggle": tr("按一下切换")},
            icon=FluentIcon.FINGERPRINT)
        grp.addSettingCard(trigger_mode)
        # 静音自动停止 + 判定阈值: 紧跟在触发模式(按下切换)之后, 仅切换模式显示
        grp.addSettingCard(silence_card)
        silence_threshold_card = NumberCard(
            tr("静音音量阈值"), tr("判定静音的音量标准; 麦底噪大就调高"),
            lambda: s.get("recognition", "silence_rms_threshold"),
            lambda v: s.set("recognition", "silence_rms_threshold", v),
            lo=0, hi=0.1, step=0.01, icon=FluentIcon.VOLUME)
        grp.addSettingCard(silence_threshold_card)
        toggle_now = s.get("trigger", "mode") == "toggle"
        silence_card.setVisible(toggle_now)             # 初始按当前模式
        silence_threshold_card.setVisible(toggle_now)
        delay_card = NumberCard(
            tr("松开延迟"), tr("松开按键后延迟结束识别, 避免句尾语气词被截掉"),
            lambda: s.get("trigger", "release_delay"),
            lambda v: s.set("trigger", "release_delay", v),
            lo=0, hi=2, step=0.1, icon=FluentIcon.SPEED_MEDIUM)
        delay_card.set_after_change(self._hotkey_reload)
        grp.addSettingCard(delay_card)
        grp.addSettingCard(HotkeyCard(
            tr("PC 快捷键"), tr("录制式快捷键: 点击后按下组合键, 实时显示, 全部松开自动保存。"),
            lambda: s.get("trigger", "pc_hotkey"),
            lambda v: (s.set("trigger", "pc_hotkey", v), self._hotkey_reload()),
            icon=FluentIcon.EXPRESSIVE_INPUT_ENTRY))
        vr_action_card = LineCard(
            tr("VR 动作名"), tr("SteamVR 动作名称"),
            lambda: s.get("trigger", "vr_action"), lambda v: s.set("trigger", "vr_action", v),
            placeholder="HoldToTalk", icon=FluentIcon.CONNECT)
        grp.addSettingCard(SwitchCard(
            tr("VR 控制器触发"), tr("默认按下左摇杆触发; 触发模式(按住/切换)与 PC 共用; 可在 SteamVR 设置→控制器→绑定布局中修改"),
            lambda: s.get("trigger", "vr_enabled"),
            lambda v: (s.set("trigger", "vr_enabled", v), vr_action_card.setVisible(v)),
            icon=FluentIcon.GAME))
        grp.addSettingCard(vr_action_card)
        vr_action_card.setVisible(s.get("trigger", "vr_enabled"))
        layout.addWidget(grp)

        grp = VRCSettingCardGroup(tr("输出设置"), self.settings_page)
        # 双发模式已废弃: 旧配置残留 both 时一次转发到 OSC, 防止隐藏双发
        if s.get("output", "mode") == "both":
            s.set("output", "mode", "osc")
        mode_card = ComboCard(
            tr("输出模式"), tr("OSC=气泡+进频道 / 剪贴板=复制后手动粘贴"),
            [tr("OSC 头顶气泡"), tr("剪贴板粘贴")],
            lambda: s.get("output", "mode"),
            lambda v: s.set("output", "mode", v),
            value_map={"osc": tr("OSC 头顶气泡"), "keyboard": tr("剪贴板粘贴")},
            icon=FluentIcon.MEGAPHONE)
        grp.addSettingCard(mode_card)
        osc_cards = [
            LineCard(tr("OSC 地址"), tr("默认 127.0.0.1"),
                     lambda: s.get("output", "osc_host"), lambda v: s.set("output", "osc_host", v),
                     icon=FluentIcon.IOT),
            NumberCard(tr("OSC 端口"), tr("默认 9000"), lambda: s.get("output", "osc_port"),
                       lambda v: s.set("output", "osc_port", int(v)), 1, 65535, 1,
                       icon=FluentIcon.TAG),
            SwitchCard(tr("录音时显示\"正在输入\""), tr("VRChat 显示输入中"),
                       lambda: s.get("output", "osc_typing_indicator"),
                       lambda v: s.set("output", "osc_typing_indicator", v),
                       icon=FluentIcon.PENCIL_INK),
            LineCard(tr("没听清提示文本"), tr("没识别到内容时显示的文本(可自定义)"),
                     lambda: s.get("output", "not_heard_text"),
                     lambda v: s.set("output", "not_heard_text", v),
                     placeholder=tr("没听清, 再说一次?"), icon=FluentIcon.EDIT),
        ]
        kb_cards = [
            SwitchCard(tr("桌面悬浮窗提醒"), tr("识别时屏幕底部显示状态(剪贴板模式没有 VRChat 正在输入提示, 靠它)"),
                       lambda: s.get("output", "desktop_overlay"),
                       lambda v: s.set("output", "desktop_overlay", v),
                       icon=FluentIcon.APPLICATION),
        ]
        for card in osc_cards:
            grp.addSettingCard(card)
        for card in kb_cards:
            grp.addSettingCard(card)

        def _apply_output(v):
            is_osc = (v == "osc")
            for card in osc_cards:
                card.setVisible(is_osc)
            for card in kb_cards:
                card.setVisible(not is_osc)
            grp._force_relayout()
        mode_card.set_after_change(_apply_output)
        _apply_output(s.get("output", "mode"))
        layout.addWidget(grp)


        grp = VRCSettingCardGroup(tr("VR 悬浮窗"), self.settings_page)
        grp.addSettingCard(SwitchCard(
            tr("启用 VR 悬浮窗"), tr("VR 内显示识别状态悬浮窗"),
            lambda: s.get("vr_overlay", "enabled"), lambda v: s.set("vr_overlay", "enabled", v),
            icon=FluentIcon.VIEW))
        grp.addSettingCard(NumberCard(
            tr("悬浮窗缩放"), tr("0.5~3.0"), lambda: s.get("vr_overlay", "scale"),
            lambda v: s.set("vr_overlay", "scale", v), 0.5, 3.0, 0.1,
            icon=FluentIcon.ZOOM_IN))
        grp.addSettingCard(NumberCard(
            tr("水平位置"), tr("0=左 0.5=中 1=右"), lambda: s.get("vr_overlay", "x"),
            lambda v: s.set("vr_overlay", "x", v), 0.0, 1.0, 0.05,
            icon=FluentIcon.LEFT_ARROW))
        grp.addSettingCard(NumberCard(
            tr("垂直位置"), tr("0=上 0.5=中 1=下"), lambda: s.get("vr_overlay", "y"),
            lambda v: s.set("vr_overlay", "y", v), 0.0, 1.0, 0.05,
            icon=FluentIcon.ARROW_DOWN))
        grp.addSettingCard(NumberCard(
            tr("发送后自动隐藏(秒)"), tr("0=不隐藏"), lambda: s.get("vr_overlay", "auto_hide_sec"),
            lambda v: s.set("vr_overlay", "auto_hide_sec", v), 0, 60, 1,
            icon=FluentIcon.QUIET_HOURS))
        grp.addSettingCard(NumberCard(
            tr("待机提示自动隐藏(秒)"), tr("空闲提示显示时长, 0=不隐藏"),
            lambda: s.get("vr_overlay", "idle_hide_sec"),
            lambda v: s.set("vr_overlay", "idle_hide_sec", v), 0, 60, 1,
            icon=FluentIcon.STOP_WATCH))
        layout.addWidget(grp)

        # ---- 通用 ----
        grp = VRCSettingCardGroup(tr("通用"), self.settings_page)
        grp.addSettingCard(ComboCard(
            tr("主题"), "", [tr("跟随系统"), tr("浅色"), tr("深色")],
            lambda: s.get("general", "theme"),
            lambda v: (s.set("general", "theme", v), self._apply_theme(v)),
            value_map={"auto": tr("跟随系统"), "light": tr("浅色"), "dark": tr("深色")},
            icon=FluentIcon.PALETTE))
        grp.addSettingCard(ComboCard(
            tr("界面语言"), tr("切换后重启应用生效"),
            [tr("简体中文"), tr("繁體中文"), "English", "日本語"],
            lambda: s.get("general", "ui_lang"),
            lambda v: s.set("general", "ui_lang", v),
            value_map={"zh-CN": tr("简体中文"), "zh-TW": tr("繁體中文"),
                       "en-US": "English", "ja": "日本語"},
            icon=FluentIcon.LANGUAGE))
        grp.addSettingCard(SwitchCard(
            tr("开机自启"), tr("登录 Windows 时自动启动"),
            autostart.is_enabled, autostart.set_enabled,
            icon=FluentIcon.POWER_BUTTON))
        grp.addSettingCard(SwitchCard(
            tr("启动时最小化到托盘"), "", lambda: s.get("general", "start_minimized"),
            lambda v: s.set("general", "start_minimized", v),
            icon=FluentIcon.MINIMIZE))
        grp.addSettingCard(SwitchCard(
            tr("启用系统托盘"), tr("关掉就只剩窗口"), lambda: s.get("general", "tray_enabled"),
            lambda v: s.set("general", "tray_enabled", v),
            icon=FluentIcon.CAFE))
        grp.addSettingCard(SwitchCard(
            tr("自动更新"), tr("启动时检查更新, 有新版本在主页顶部提醒"),
            lambda: s.get("general", "auto_update"),
            lambda v: s.set("general", "auto_update", v),
            icon=FluentIcon.UPDATE))
        layout.addWidget(grp)

        debug_grp = ExpandGroupSettingCard(
            FluentIcon.DEVELOPER_TOOLS, tr("调试"),
            tr("一般人你应该用不上这些"), self.settings_page)
        debug_grp.addGroupWidget(DebugStatusCard(s))
        debug_grp.addGroupWidget(ButtonCard(
            tr("OSC 测试"), tr("发一条测试气泡到 VRChat chatbox"),
            tr("测试"), self._osc_test, icon=FluentIcon.SEND))
        debug_grp.addGroupWidget(SwitchCard(
            tr("无视 VRChat 检测"),
            tr("关: VRChat 未运行时拦截触发; 开: 不启动 VRChat 也能测试"),
            lambda: s.get("debug", "ignore_vrc_check"),
            lambda v: s.set("debug", "ignore_vrc_check", v),
            icon=FluentIcon.PEOPLE))
        debug_grp.addGroupWidget(SwitchCard(
            tr("显示 VR 心跳日志"),
            tr("开启: 每 3 秒打印 VR 通道状态(排障用); 关: 默认不刷屏"),
            lambda: s.get("debug", "show_heartbeat_log"),
            lambda v: s.set("debug", "show_heartbeat_log", v),
            icon=FluentIcon.HEART))
        debug_grp.addGroupWidget(SwitchCard(
            tr("强制检查新版本"),
            tr("开启: 即使已是最新也提示可更新(测试更新流程用)"),
            lambda: s.get("debug", "force_check_update"),
            lambda v: s.set("debug", "force_check_update", v),
            icon=FluentIcon.UPDATE))
        debug_grp.addGroupWidget(ButtonCard(
            tr("重置模型"), tr("模型加载失败或卡住时重载 ASR 引擎"),
            tr("重置"), self._reload_model, icon=FluentIcon.UPDATE))
        debug_grp.addGroupWidget(ButtonCard(
            tr("强制显示悬浮窗"), tr("不按摇杆也能调出悬浮窗(需 SteamVR 运行)"),
            tr("显示"), self._debug_overlay_show, icon=FluentIcon.VIEW))
        debug_grp.addGroupWidget(ButtonCard(
            tr("强制隐藏悬浮窗"), tr("手动收起悬浮窗"),
            tr("隐藏"), self._debug_overlay_hide, icon=FluentIcon.HIDE))
        debug_grp.addGroupWidget(ButtonCard(
            tr("打开配置文件"), tr("config.json, 改完重启生效"),
            tr("打开"), self._debug_open_config, icon=FluentIcon.DOCUMENT))
        debug_grp.addGroupWidget(ButtonCard(
            tr("环境信息"), tr("运行模式/路径/Python 版本"),
            tr("查看"), self._debug_env_info, icon=FluentIcon.CODE))
        debug_grp.addGroupWidget(ButtonCard(
            tr("OSC 地址"), tr("默认 127.0.0.1"),
            tr("重置"), self._debug_reset_config, icon=FluentIcon.DELETE))
        layout.addWidget(debug_grp)
        layout.addStretch(1)
        self._refresh_mic_items()

    def _build_polish_page(self):
        """AI 润色"""
        s = self.settings
        scroll_widget = QWidget()
        self.polish_page.setWidget(scroll_widget)
        scroll_widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(scroll_widget)
        layout.setContentsMargins(24, 12, 24, 16)
        layout.setSpacing(10)

        self.polish_banner = self._make_model_banner(self.polish_page)
        layout.addWidget(self.polish_banner)

        grp = VRCSettingCardGroup(tr("AI 润色设置"), self.polish_page)

        def _set_polish_enabled(v):
            s.set("polish", "enabled", v)
            self._update_model_banner("polish")

        self.polish_enable_card = SwitchCard(
            tr("启用润色"), tr("识别后润色再发送(需在「AI 设置」添加模型); 松手后先填框不发送, 润色完替换发送"),
            lambda: s.get("polish", "enabled"), _set_polish_enabled)
        grp.addSettingCard(self.polish_enable_card)
        self.polish_model_card = ComboCard(
            tr("使用模型"), tr("用哪个模型润色; 模型在「AI 设置」页添加(云端/本地多家都可以)"),
            s.ai_model_names(),
            lambda: s.get("polish", "use_model"),
            lambda v: s.set("polish", "use_model", v),
            icon=FluentIcon.ROBOT)
        grp.addSettingCard(self.polish_model_card)
        style_card = ComboCard(
            tr("润色风格"), tr("选'自定义'可用提示词描述想要的风格"),
            [tr("轻度润色"), tr("仅整理"), tr("正式"), tr("自定义")],
            lambda: s.get("polish", "style"),
            lambda v: s.set("polish", "style", v),
            value_map={"light": tr("轻度润色"), "raw": tr("仅整理"),
                       "formal": tr("正式"), "custom": tr("自定义")})
        grp.addSettingCard(style_card)
        style_prompt_card = TextAreaCard(
            tr("自定义润色风格"), tr("用提示词描述想要的润色方式, 如: 活泼一点, 多用语气词, 像朋友聊天"),
            lambda: s.get("polish", "custom_prompt"),
            lambda v: s.set("polish", "custom_prompt", v),
            placeholder=tr("例如: 更活泼, 多用语气词, 像朋友聊天"))
        grp.addSettingCard(style_prompt_card)
        style_prompt_card.setVisible(s.get("polish", "style") == "custom")
        style_card.set_after_change(lambda raw: style_prompt_card.setVisible(raw == "custom"))
        layout.addWidget(grp)
        layout.addStretch(1)
        self.style_card = style_card
        self.style_prompt_card = style_prompt_card
        self._refresh_model_combos()

    def _build_translate_page(self):
        """AI 翻译"""
        s = self.settings
        scroll_widget = QWidget()
        self.translate_page.setWidget(scroll_widget)
        scroll_widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(scroll_widget)
        layout.setContentsMargins(24, 12, 24, 16)
        layout.setSpacing(10)

        self.tr_banner = self._make_model_banner(self.translate_page)
        layout.addWidget(self.tr_banner)

        grp = VRCSettingCardGroup(tr("AI 翻译设置"), self.translate_page)

        def _set_tr_enabled(v):
            """开启翻译前先校验配置完整, 不完整则提示并回弹开关, 杜绝静默失败/挂起感。"""
            if v:
                ok, tip = self.controller.translate_config_ready()
                if not ok:
                    InfoBar.warning(tr("翻译配置不完整, 未启用"), tip, parent=self,
                                    duration=8000)
                    self.tr_enable_card.switch.setChecked(False)  # 回弹
                    return
            s.set("translate", "enabled", v)
            self._update_model_banner("translate")

        self.tr_enable_card = SwitchCard(
            tr("启用翻译"), tr("把润色/识别结果再翻译成目标语言再发送"),
            lambda: s.get("translate", "enabled"),
            _set_tr_enabled)
        grp.addSettingCard(self.tr_enable_card)
        # 启动校验: 旧配置已启用但配置不完整 -> 自动关闭并提示
        if s.get("translate", "enabled"):
            ok, tip = self.controller.translate_config_ready()
            if not ok:
                s.set("translate", "enabled", False)
                QTimer.singleShot(600, lambda: InfoBar.warning(
                    tr("翻译配置不完整, 已自动关闭"), tip, parent=self, duration=8000))
        self.tr_model_card = ComboCard(
            tr("使用模型"), tr("用哪个模型润色; 模型在「AI 设置」页添加(云端/本地多家都可以)"),
            s.ai_model_names(),
            lambda: s.get("translate", "use_model"),
            lambda v: s.set("translate", "use_model", v),
            icon=FluentIcon.ROBOT)
        grp.addSettingCard(self.tr_model_card)
        mode_card = ComboCard(
            tr("输出方式"), tr("仅译文=只发翻译后的; 双语对照=原文和译文一起发"),
            [tr("仅译文"), tr("双语对照")],
            lambda: s.get("translate", "output_mode"),
            lambda v: s.set("translate", "output_mode", v),
            value_map={"target_only": tr("仅译文"), "bilingual": tr("双语对照")},
            icon=FluentIcon.CHAT)
        grp.addSettingCard(mode_card)
        separator_card = LineCard(
            tr("双语分隔符"), tr("双语对照时原文和译文之间的间隔"),
            lambda: s.get("translate", "separator"),
            lambda v: s.set("translate", "separator", v),
            placeholder=" / ")
        grp.addSettingCard(separator_card)
        lang_card = LineCard(
            tr("目标语言"), tr("翻译成什么语言, 直接填名称或描述"),
            lambda: s.get("translate", "target_lang"),
            lambda v: s.set("translate", "target_lang", v),
            placeholder=tr("英文 / 日语 / 韩语 / 法语..."))
        grp.addSettingCard(lang_card)
        separator_card.setVisible(s.get("translate", "output_mode") == "bilingual")
        mode_card.set_after_change(lambda raw: separator_card.setVisible(raw == "bilingual"))
        layout.addWidget(grp)
        layout.addStretch(1)
        self._refresh_model_combos()

        # 翻译测试
        sample = tr("今天天气不错, 我想出去走走。")
        lang = s.get("translate", "target_lang") or tr("英文")

        def _test_translate():
            if not s.get("translate", "enabled"):
                InfoBar.warning(tr("请填模型名"), "", parent=self)
                return
            ok, tip = self.controller.translate_config_ready()
            if not ok:
                InfoBar.warning(tr("翻译配置不完整"), tip, parent=self, duration=8000)
                return
            lang = s.get("translate", "target_lang") or tr("英文")

            def _run():
                # 后台线程: 只做网络请求, 绝不碰 QWidget; 结果回主线程显示
                try:
                    out = self.controller._translate(sample)
                except Exception as e:
                    self._tr_test_done.emit(
                        "error", tr("翻译测试失败: {err}", err=e))
                    return
                if not out:
                    self._tr_test_done.emit(
                        "error", tr("翻译测试失败: {detail}",
                                    detail=tr("检查引擎配置(地址/Key/模型)")))
                    return
                self._tr_test_done.emit("ok", out)

            import threading
            threading.Thread(target=_run, daemon=True).start()

        self._tr_test_done.connect(self._on_tr_test_done)
        grp.addSettingCard(ButtonCard(
            tr("翻译测试"), tr("把「{sample}」翻译成{lang}并显示结果", sample=sample, lang=lang), tr("翻译"), _test_translate,
            icon=FluentIcon.SEND))

    def _on_tr_test_done(self, state, text):
        """主线程: 显示翻译测试结果。"""
        if state == "ok":
            lang = self.settings.get("translate", "target_lang") or tr("英文")
            InfoBar.success(f"[{lang}] {text}", "", parent=self, duration=8000)
        else:
            InfoBar.error(text, "", parent=self, duration=8000)

    def _build_ai_models_page(self):
        """AI 设置"""
        s = self.settings
        scroll_widget = QWidget()
        self.ai_models_page.setWidget(scroll_widget)
        scroll_widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(scroll_widget)
        layout.setContentsMargins(24, 12, 24, 16)
        layout.setSpacing(10)

        self.ai_models_grp = VRCSettingCardGroup(tr("我的模型"), self.ai_models_page)
        layout.addWidget(self.ai_models_grp)

        self._ai_models_empty = SettingCard(
            FluentIcon.INFO, tr("还没有模型"), tr("点下面「添加模型」加第一家吧"), self.ai_models_page)
        layout.addWidget(self._ai_models_empty)

        row = QHBoxLayout()
        add_btn = PrimaryPushButton(tr("+ 添加模型"))
        add_btn.clicked.connect(self._add_model)
        row.addWidget(add_btn)
        self._test_all_btn = PushButton(tr("测试全部模型"))
        self._test_all_btn.setToolTip(tr("逐个测试模型连通性, 通过变绿, 有问题变红"))
        self._test_all_btn.clicked.connect(self._test_all_models)
        self._test_done.connect(self._on_test_done)
        row.addWidget(self._test_all_btn)
        row.addStretch(1)
        layout.addLayout(row)

        layout.addStretch(1)
        self._model_cards = []
        self._rebuild_model_list()

    def _add_model(self):
        dlg = _ModelEditDialog(self.settings, self.controller, parent=self)
        if dlg.exec() and dlg.result_entry:
            self.settings.ai_models().append(dlg.result_entry)
            self.settings.save()
            self._rebuild_model_list()
            self._refresh_model_combos()
            InfoBar.success(tr("模型已添加"), dlg.result_entry["name"], parent=self)

    def _edit_model(self, m: dict):
        dlg = _ModelEditDialog(self.settings, self.controller, entry=m, parent=self)
        if not dlg.exec():
            return
        if dlg.deleted:
            name = m.get("name", "")
            lst = self.settings.ai_models()
            self.settings.ai_models()[:] = [x for x in lst if x is not m]
            for section in ("polish", "translate"):
                if self.settings.get(section, "use_model") == name:
                    self.settings.set(section, "use_model", "")
            self.settings.save()
            self._rebuild_model_list()
            self._refresh_model_combos()
            InfoBar.info(tr("模型已删除"), name, parent=self)
        elif dlg.result_entry:
            old_name = m.get("name", "")
            new = dlg.result_entry
            for section in ("polish", "translate"):
                if self.settings.get(section, "use_model") == old_name:
                    self.settings.set(section, "use_model", new["name"])
            m.clear()
            m.update(new)
            self.settings.save()
            self._rebuild_model_list()
            self._refresh_model_combos()
            InfoBar.success(tr("模型已更新"), new["name"], parent=self)

    def _rebuild_model_list(self):
        """AI 设置"""
        grp = self.ai_models_grp
        grp.clear()
        cards = []
        for m in self.settings.ai_models():
            name = m.get("name", "?")
            kind = m.get("kind", "cloud")
            model = m.get("model", "")
            host = ""
            try:
                from urllib.parse import urlparse
                host = urlparse(m.get("endpoint", "") or "").netloc
            except Exception:
                pass
            tag = tr("云端") if kind == "cloud" else tr("本地")
            detail = f"{tag} · {model}" + (f" · {host}" if host else "")
            card = ButtonCard(
                name, detail, tr("编辑"),
                lambda _m=m: self._edit_model(_m),
                icon=FluentIcon.CLOUD if kind == "cloud" else FluentIcon.APPLICATION)
            card.setObjectName("modelCard")
            grp.addSettingCard(card)
            cards.append(card)
        self._model_cards = cards
        self._ai_models_empty.setVisible(not cards)
        grp._force_relayout()

    def _test_all_models(self):
        """一键测试: 后台线程逐个探活, 结果回主线程上色(绿=通过/红=有问题)。"""
        models = self.settings.ai_models()
        if not models:
            InfoBar.warning(tr("没有模型"), tr("先添加模型再测试"), parent=self)
            return
        for idx in range(len(models)):
            self._set_model_card_state(idx, "testing")
        self._test_all_btn.setEnabled(False)
        self._test_all_btn.setText(tr("测试中..."))

        def _run_all():
            results = []
            for idx, m in enumerate(models):
                results.append((idx,) + self._probe_model(m))
            self._test_done.emit(results)

        threading.Thread(target=_run_all, daemon=True).start()

    @staticmethod
    def _probe_model(m):
        """探活一个模型: GET {base}/v1/models, 2xx=连通。后台线程内调用。"""
        import re as _re
        import urllib.request
        import urllib.error
        ep = (m.get("endpoint") or "").strip()
        key = (m.get("api_key") or "").strip()
        try:
            timeout = max(3, min(int(m.get("timeout_sec") or 8), 15))
        except Exception:
            timeout = 8
        # 去掉 API 路径后缀: 兼容 /v1/chat/completions、/chat/completions(无/v1)、
        # 结尾 /v1、裸根地址 四种写法
        base = _re.sub(r"/v1/chat/completion[s]?/?$", "", ep)  # /v1/chat/completions -> 根
        base = _re.sub(r"/chat/completion[s]?/?$", "", base)  # 不带 /v1 的写法(含手误单数)
        base = _re.sub(r"/?v1/?$", "", base) or ep            # 兜底: 结尾 /v1
        url = base.rstrip("/") + "/v1/models"
        req = urllib.request.Request(
            url, headers={"Authorization": "Bearer " + key} if key else {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if 200 <= resp.status < 300:
                    return True, tr("HTTP {code}", code=resp.status)
                return False, tr("HTTP {code}", code=resp.status)
        except urllib.error.HTTPError as e:
            return False, tr("HTTP {code}", code=e.code)
        except Exception as e:
            return False, str(e)[:60]

    def _on_test_done(self, results):
        """主线程: 全部探活完成, 上色 + 汇总。"""
        ok_count = 0
        for idx, ok, msg in results:
            self._set_model_card_state(idx, "ok" if ok else "fail", msg)
            ok_count += 1 if ok else 0
        btn = getattr(self, "_test_all_btn", None)
        if btn is not None:
            btn.setEnabled(True)
            btn.setText(tr("测试全部模型"))
        total = len(results)
        if ok_count == total:
            InfoBar.success(tr("全部连通"), tr("所有模型测试通过"), parent=self)
        else:
            InfoBar.error(tr("测试完成"),
                          tr("{ok}/{total} 个模型连通", ok=ok_count, total=total),
                          parent=self)

    def _set_model_card_state(self, idx, state, msg=""):
        """模型卡片状态: testing=默认 / ok=绿边绿字 / fail=红边红字。"""
        card = self._model_cards[idx]
        m = self.settings.ai_models()[idx]
        kind = m.get("kind", "cloud")
        model = m.get("model", "")
        host = ""
        try:
            from urllib.parse import urlparse
            host = urlparse(m.get("endpoint", "") or "").netloc
        except Exception:
            pass
        tag = tr("云端") if kind == "cloud" else tr("本地")
        base = f"{tag} · {model}" + (f" · {host}" if host else "")
        card.titleLabel.setText(m.get("name", "?"))
        if state == "testing":
            card.contentLabel.setText(base + " · " + tr("测试中..."))
            card.setStyleSheet("")
        elif state == "ok":
            card.contentLabel.setText(base + " · " + tr("连通") + f" {msg}")
            card.setStyleSheet(
                "QFrame#modelCard{border:2px solid #4ade80;border-radius:8px;}")
        else:
            card.contentLabel.setText(base + " · " + tr("有问题") + f": {msg}")
            card.setStyleSheet(
                "QFrame#modelCard{border:2px solid #f87171;border-radius:8px;}")

    def _refresh_model_combos(self):
        """        """
        names = self.settings.ai_model_names()
        # 设置页云端识别模型下拉 + 无模型警告横幅
        rec_card = getattr(self, "cloud_model_card", None)
        if rec_card is not None:
            rec_card.set_models(names, self.settings.get("recognition", "cloud_model"))
        rec_banner = getattr(self, "rec_banner", None)
        if rec_banner is not None:
            rec_banner.setVisible(not bool(names))
        for card_attr, section in (("polish_model_card", "polish"),
                                   ("tr_model_card", "translate")):
            card = getattr(self, card_attr, None)
            if card is None:
                continue
            card.combo.blockSignals(True)
            cur = self.settings.get(section, "use_model")
            card.combo.clear()
            card.combo.addItems(names)
            if cur in names:
                card.combo.setCurrentText(cur)
            elif names and not cur:
                # 没选过: 默认用第一个, 直接写回配置(否则显示有值但实际不生效)
                card.combo.setCurrentText(names[0])
                self.settings.set(section, "use_model", names[0])
            card.combo.blockSignals(False)
            card.setVisible(bool(names))
            self._update_model_banner(section)
        try:
            self.ai_models_grp._force_relayout()
        except Exception:
            pass

    _BANNER_RED = """QFrame#modelBanner {
                background-color: #FDECEA;
                border: 1px solid #F5C6C0;
                border-radius: 8px;
            }
            QLabel#mbTitle {
                color: #B3261E; font-size: 14px; font-weight: 600;
                background: transparent; border: none;
            }
            QLabel#mbSub {
                color: #B0655E; font-size: 12px;
                background: transparent; border: none;
            }"""

    _BANNER_GREEN = """QFrame#modelBanner {
                background-color: #DCFCE7;
                border: 1px solid #86EFAC;
                border-radius: 8px;
            }
            QLabel#mbTitle {
                color: #166534; font-size: 14px; font-weight: 600;
                background: transparent; border: none;
            }
            QLabel#mbSub {
                color: #15803D; font-size: 12px;
                background: transparent; border: none;
            }"""

    def sync_polish_from_settings(self, checked=None):
        """托盘切润色后同步设置页开关与横幅(checked 可选, 兼容 Signal 调用)。
        blockSignals: 纯视觉同步, 不触发 checkedChanged 连锁(避免配置校验回弹)。"""
        try:
            sw = self.polish_enable_card.switch
            sw.blockSignals(True)
            sw.setChecked(bool(self.settings.get("polish", "enabled")))
            sw.blockSignals(False)
            self._update_model_banner("polish")
        except Exception:
            pass

    def sync_translate_from_settings(self, checked=None):
        """托盘切翻译后同步设置页开关与横幅。"""
        try:
            sw = self.tr_enable_card.switch
            sw.blockSignals(True)
            sw.setChecked(bool(self.settings.get("translate", "enabled")))
            sw.blockSignals(False)
            self._update_model_banner("translate")
        except Exception:
            pass

    def _make_model_banner(self, parent):
        """顶部提醒横幅(放在页面最顶, 与设置项隔开): 没配置模型=淡红; 正常工作=淡绿。"""
        frame = QFrame(parent)
        frame.setObjectName("modelBanner")
        frame.setStyleSheet(self._BANNER_RED)
        h = QHBoxLayout(frame)
        h.setContentsMargins(16, 12, 16, 12)
        h.setSpacing(10)
        icon = IconWidget(FluentIcon.MESSAGE)
        icon.setFixedSize(20, 20)
        icon.setFixedSize(20, 20)
        h.addWidget(icon)
        v = QVBoxLayout()
        v.setSpacing(2)
        title = QLabel("")
        title.setObjectName("mbTitle")
        sub = QLabel("")
        sub.setObjectName("mbSub")
        v.addWidget(title)
        v.addWidget(sub)
        h.addLayout(v)
        h.addStretch(1)
        frame._mb_icon = icon
        frame._mb_title = title
        frame._mb_sub = sub
        frame.hide()
        return frame

    def _update_model_banner(self, section):
        """按状态刷新顶部提醒: 没配置模型->淡红提醒; 启用工作->淡红"正在工作!"; 未启用->隐藏。"""
        banner = getattr(self, "polish_banner" if section == "polish" else "tr_banner", None)
        card = getattr(self, "polish_model_card" if section == "polish" else "tr_model_card", None)
        if banner is None or card is None:
            return
        names = self.settings.ai_model_names()
        has_model = bool(names) and card.combo.currentText() in names
        if not has_model:
            banner.setStyleSheet(self._BANNER_RED)
            banner._mb_icon.setIcon(FluentIcon.MESSAGE)
            banner._mb_title.setText(tr("没有配置模型"))
            banner._mb_sub.setText(tr("请到「AI 设置」页添加模型(云端/本地都可以), 添加后回来就能用"))
            banner.show()
        elif self.settings.get(section, "enabled"):
            banner.setStyleSheet(self._BANNER_GREEN)
            banner._mb_icon.setIcon(FluentIcon.CHECKBOX)
            banner._mb_title.setText(tr("AI 润色正在工作！") if section == "polish" else tr("AI 翻译正在工作！"))
            banner._mb_sub.setText(tr("当前模型: ") + card.combo.currentText())
            banner.show()
        else:
            banner.hide()

    def _refresh_mic_items(self, *args):
        try:
            from ..recorder import Recorder
            devs = Recorder.list_devices()
            items = [tr("(默认)")] + [d[0] for d in devs]
            self.mic_card.combo.blockSignals(True)
            self.mic_card.combo.clear()
            self.mic_card.combo.addItems(items)
            cur = self.settings.get("recognition", "mic_device") or tr("(默认)")
            if cur in items:
                self.mic_card.combo.setCurrentText(cur)
            self.mic_card.combo.blockSignals(False)
        except Exception:
            pass

    def _hotkey_reload(self):
        if self._on_hotkey_changed:
            self._on_hotkey_changed()

    def _apply_theme(self, theme: str):
        try:
            if theme == "light":
                setTheme(Theme.LIGHT)
            elif theme == "dark":
                setTheme(Theme.DARK)
            else:
                setTheme(Theme.AUTO)
        except Exception:
            pass

    def showEvent(self, e):
        super().showEvent(e)
        # 窗口真正显示后再强制一次布局(激活 ExpandLayout 不自动排布的坑)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._relayout_all)

    def _relayout_all(self):
        try:
            sw = self.settings_page.widget()
            if sw and sw.layout():
                sw.layout().activate()
            for grp in self.settings_page.findChildren(VRCSettingCardGroup):
                grp._force_relayout()
        except Exception as e:
            print(f"[gui] 重排失败: {e}")

    def closeEvent(self, event):
        if self.settings.get("general", "tray_enabled") and self.tray_visible:
            event.ignore()
            self.hide()
        else:
            event.accept()

