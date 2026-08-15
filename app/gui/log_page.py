"""日志页: 实时显示 vrcvoice.log 尾部输出, 方便用户快速定位问题。

增量读文件(记住字节偏移), 每秒轮询一次; 文件被截断/重建时自动重来。
只清显示不影响日志文件本身。
"""
import os
import subprocess

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from ..log import LOG_PATH
from ..i18n import tr

MAX_BLOCKS = 5000      # 显示上限行数, 超出丢最旧
INITIAL_TAIL = 200 * 1024  # 首次只显示文件尾部 200KB, 避免大文件卡顿


class LogPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._offset = 0
        self._size = 0
        self._init_ui()
        try:
            size = os.path.getsize(LOG_PATH)
            self._offset = max(0, size - INITIAL_TAIL)
        except OSError:
            self._offset = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(1000)
        self._poll()

    def _init_ui(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 12, 16, 12)
        v.setSpacing(8)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.btn_open = QPushButton(tr("打开文件位置"))
        self.btn_copy = QPushButton(tr("复制全部"))
        self.btn_clear = QPushButton(tr("清空显示"))
        self.btn_open.clicked.connect(self._open_file)
        self.btn_copy.clicked.connect(self._copy_all)
        self.btn_clear.clicked.connect(lambda: self._view.clear())
        bar.addWidget(self.btn_open)
        bar.addWidget(self.btn_copy)
        bar.addWidget(self.btn_clear)
        bar.addStretch(1)
        self.lbl_path = QLabel(LOG_PATH)
        self.lbl_path.setStyleSheet("color:gray;font-size:12px;")
        self.lbl_path.setToolTip(LOG_PATH)
        bar.addWidget(self.lbl_path)
        v.addLayout(bar)

        self._view = QPlainTextEdit()
        self._view.setReadOnly(True)
        self._view.setMaximumBlockCount(MAX_BLOCKS)
        self._view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        font = QFont("Consolas")
        font.setPointSize(9)
        self._view.setFont(font)
        # 终端风深色底, 亮/暗主题下都协调
        self._view.setStyleSheet(
            "QPlainTextEdit{background:#171a20;color:#d4d8e0;"
            "border:1px solid #2d3340;border-radius:8px;"
            "selection-background-color:#3d5a80;padding:6px;}")
        v.addWidget(self._view, 1)

    def _poll(self):
        """增量读新增日志。seek 往前回退 4 字节再取到首个换行后, 避免
        UTF-8 多字节字符被拦腰截断产生乱码。"""
        try:
            size = os.path.getsize(LOG_PATH)
        except OSError:
            return
        if size < self._size:  # 文件被截断/重建
            self._offset = 0
            self._view.clear()
        self._size = size
        if size <= self._offset:
            return
        read_from = max(0, self._offset - 4)
        try:
            with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
                f.seek(read_from)
                data = f.read()
        except OSError:
            return
        self._offset = size
        if read_from > 0:
            nl = data.find("\n")
            if nl < 0:
                return
            data = data[nl + 1:]
        data = data.rstrip("\n")
        if not data:
            return
        at_bottom = self._view.verticalScrollBar().value() >= \
            self._view.verticalScrollBar().maximum() - 8
        self._view.appendPlainText(data)
        if at_bottom:
            self._view.verticalScrollBar().setValue(
                self._view.verticalScrollBar().maximum())

    def _open_file(self):
        # 在资源管理器中定位并选中日志文件
        try:
            subprocess.Popen(["explorer", "/select,", LOG_PATH])
        except OSError:
            pass

    def _copy_all(self):
        QApplication.clipboard().setText(self._view.toPlainText())
