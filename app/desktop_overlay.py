"""PC 桌面悬浮窗: 识别状态提醒。
键盘输入模式没有 VRChat 的"正在输入"表情, 用桌面悬浮窗提醒识别中/已发送/出错。
无边框半透明卡片, 置顶显示在屏幕底部中央, 识别中呼吸闪烁。
"""
from PySide6.QtCore import Qt, QTimer, QObject, Signal, QRect
from PySide6.QtWidgets import QWidget, QLabel, QGraphicsOpacityEffect
from PySide6.QtGui import QPainter, QColor, QFont, QFontMetrics


class _DesktopBridge(QObject):
    """跨线程安全桥: 后台线程(controller._feed_loop 等)调用公共方法 → emit 信号
    → QueuedConnection → 主线程执行真正的窗口操作。
    实测: 后台线程直接调 show/raise_ 会卡死(Windows 窗口系统跨线程竞争) → 必须信号化。"""
    recording = Signal(str)
    result = Signal(str, bool, int)
    polishing = Signal(str)
    reset = Signal()


class DesktopOverlay(QWidget):
    """状态: recording(呼吸) / result(绿色) / error(红色), 结果 3 秒后自动隐藏。"""

    def __init__(self, parent=None, settings=None):
        super().__init__(parent)
        self._settings = settings  # 用于判断输出模式: 剪贴板模式成功文案显示"已复制"
        self._bridge = _DesktopBridge()
        self._bridge.recording.connect(self._on_recording)
        self._bridge.result.connect(self._on_result)
        self._bridge.polishing.connect(self._on_polishing)
        self._bridge.reset.connect(self._on_reset)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(560, 100)
        self._label = QLabel("", self)
        self._label.setGeometry(0, 0, 560, 100)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet(
            "color: white; font-size: 24px; font-weight: 600;")
        self._opacity = QGraphicsOpacityEffect(self)
        self._label.setGraphicsEffect(self._opacity)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)
        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._pulse)
        self._pulse_dir = -1
        self._pulse_val = 1.0
        self._success = False  # result 成功后 True → 画绿边
        self._move_to_bottom_center()
        self.hide()

    def _show_text_grow(self, text: str, color: str, size: int, max_lines: int = 3):
        """多行显示: 自动换行, 窗口随行数增高(上限 max_lines 行), 超出截断加省略号。
        避免长文本超出悬浮框。"""
        f = QFont("Microsoft YaHei")
        f.setPixelSize(size)
        f.setWeight(QFont.Weight.DemiBold)
        self._label.setFont(f)
        self._label.setStyleSheet(f"color: {color};")
        self._label.setWordWrap(True)
        fm = QFontMetrics(f)
        avail = self.width() - 44
        shown = text
        for _ in range(400):
            r = fm.boundingRect(QRect(0, 0, avail, 100000),
                                Qt.TextFlag.TextWordWrap, shown)
            lines = max(1, -(-r.height() // max(1, fm.lineSpacing())))
            if lines <= max_lines:
                break
            shown = shown[:-3].rstrip() + "…"
        r = fm.boundingRect(QRect(0, 0, avail, 100000),
                            Qt.TextFlag.TextWordWrap, shown)
        lines = max(1, -(-r.height() // max(1, fm.lineSpacing())))
        if lines <= 1:
            h = 100  # 单行保持原高度(底部小卡片视觉)
        else:
            h = min(96 + lines * fm.lineSpacing() + 6,
                    96 + max_lines * fm.lineSpacing() + 6)
        self.setFixedSize(self.width(), h)
        self._label.setGeometry(0, 0, self.width(), h)
        self._label.setText(shown)
        self._move_to_bottom_center()

    def _move_to_bottom_center(self):
        from PySide6.QtGui import QGuiApplication
        scr = QGuiApplication.primaryScreen()
        if scr is None:
            return
        geo = scr.availableGeometry()
        self.move(geo.x() + (geo.width() - self.width()) // 2,
                  geo.y() + geo.height() - self.height() - 60)

    def _pulse(self):
        self._pulse_val += 0.15 * self._pulse_dir
        if self._pulse_val <= 0.35:
            self._pulse_val = 0.35
            self._pulse_dir = 1
        elif self._pulse_val >= 1.0:
            self._pulse_val = 1.0
            self._pulse_dir = -1
        self._opacity.setOpacity(self._pulse_val)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(18, 18, 24, 225))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(self.rect(), 16, 16)
        if self._success:  # 发送成功: 绿色双描边
            painter.setBrush(Qt.BrushStyle.NoBrush)
            pen = QColor(74, 222, 128, 230)
            painter.setPen(pen)
            painter.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), 14, 14)
            painter.setPen(QColor(74, 222, 128, 90))
            painter.drawRoundedRect(self.rect().adjusted(7, 7, -7, -7), 11, 11)

    def show_polishing(self, text: str = ""):
        """线程安全: AI 润色中状态(琥珀色 + 呼吸)。"""
        self._bridge.polishing.emit(text)

    def _on_polishing(self, text: str = ""):
        """AI 润色中。主线程执行。"""
        self._success = False
        self._hide_timer.stop()
        self._pulse_val = 1.0
        self._pulse_dir = -1
        self._opacity.setOpacity(1.0)
        if text.strip():
            self._show_text_grow(f"✦ AI 润色中: {text.strip()}", "#f5c86b", 22)
        else:
            self._show_text_grow("✦ AI 润色中", "#f5c86b", 22)
        self._pulse_timer.start(120)
        self.show()
        self.raise_()

    def show_recording(self, text: str = ""):
        """线程安全: 任意线程可调(内部信号回主线程)。"""
        self._bridge.recording.emit(text)

    def _on_recording(self, text: str = ""):
        """开始识别 / 识别中(带呼吸动画)。主线程执行。"""
        self._success = False
        self._hide_timer.stop()
        self._pulse_val = 1.0
        self._pulse_dir = -1
        self._opacity.setOpacity(1.0)
        if text.strip():
            self._show_text_grow(f"● 识别中: {text.strip()}", "#d8d8e0", 22)
        else:
            self._show_text_grow("● 识别中", "#d8d8e0", 22)
        self._pulse_timer.start(120)
        self.show()
        self.raise_()

    def show_result(self, text: str, error: bool = False, duration: int = 4000):
        """线程安全: 任意线程可调(内部信号回主线程)。"""
        self._bridge.result.emit(text, error, duration)

    def _on_result(self, text: str, error: bool = False, duration: int = 4000):
        """识别结束: 显示识别内容, 超时后自动隐藏(默认 4s, 空结果传 2s)。主线程执行。"""
        self._success = not error
        self._pulse_timer.stop()
        self._opacity.setOpacity(1.0)
        if error:
            self._show_text_grow(f"✗ {text.strip()}", "#ff6b6b", 18)
        else:
            tag = "已复制" if (self._settings and self._settings.get("output", "mode") == "keyboard") else "已发送"
            self._show_text_grow(f"✓ {tag}: {text.strip()}", "#f0f0f4", 20)
        self.show()
        self.raise_()
        self._hide_timer.start(duration)

    def reset(self):
        """线程安全: 开始新一轮识别前清掉上次结果(隐藏用)。"""
        self._bridge.reset.emit()

    def _on_reset(self):
        """主线程执行。"""
        self._success = False
        self._hide_timer.stop()
        self._pulse_timer.stop()
        self.hide()
