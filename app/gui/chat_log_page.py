# -*- coding: utf-8 -*-
"""对话记录页: 记录每局发出去的文本(原文 + AI 润色版), 像聊天记录一样展示。
数据持久化到 <APPDATA>/VRCVoice/chatlog.json, 上限 500 条(保留最新)。
所有写入都在主线程(Signal 桥保证), 文件读写用 try/except 兜底, 坏了不影响主程序。
"""
import json
import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget, QScrollArea

from qfluentwidgets import CardWidget, CaptionLabel, BodyLabel, StrongBodyLabel, InfoBar, InfoBarPosition

from ..log import log
from ..i18n import tr

MAX_ENTRIES = 500
CHATLOG_NAME = "chatlog.json"


def _chatlog_path():
    base = os.environ.get("APPDATA", "")
    return os.path.join(base, "VRCVoice", CHATLOG_NAME)


class _ClickCard(CardWidget):
    """可点击复制卡片: 点击任意处复制该条最终输出。"""
    clicked = Signal()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class ChatLogPage(QWidget):
    """对话记录: 每次发送一条卡片, 原文灰色在上, 润色版加粗在下。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("chatLogPage")
        self._entries = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self._container = QWidget()
        self._list_lay = QVBoxLayout(self._container)
        self._list_lay.setContentsMargins(16, 12, 16, 12)
        self._list_lay.setSpacing(8)
        self._list_lay.addStretch(1)
        self.scroll.setWidget(self._container)
        lay.addWidget(self.scroll)
        self._reset()

    # ---------- 数据 ----------
    def _reset(self):
        """每次启动清空历史(会话内记录, 重启即清), 显示空占位符。"""
        self._entries = []
        try:
            os.makedirs(os.path.dirname(_chatlog_path()), exist_ok=True)
            with open(_chatlog_path(), "w", encoding="utf-8") as f:
                json.dump({"entries": []}, f, ensure_ascii=False, indent=1)
        except Exception as e:
            log(f"[chatlog] 清空失败: {e}")
        self._add_empty_hint()

    def _load(self):
        try:
            with open(_chatlog_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            self._entries = [e for e in data.get("entries", [])
                             if isinstance(e, dict)
                             and ("final" in e or "original" in e)
                             and (str(e.get("final", "")).strip()
                                  or str(e.get("original", "")).strip())]
        except Exception:
            self._entries = []

    def _save(self):
        try:
            os.makedirs(os.path.dirname(_chatlog_path()), exist_ok=True)
            with open(_chatlog_path(), "w", encoding="utf-8") as f:
                json.dump({"entries": self._entries[-MAX_ENTRIES:]},
                          f, ensure_ascii=False, indent=1)
        except Exception as e:
            log(f"[chatlog] 保存失败: {e}")

    # ---------- UI ----------
    def _add_empty_hint(self):
        hint = CaptionLabel(tr("还没有发送记录, 按住说话发一条试试"))
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color: #909399; margin-top: 40px;")
        self._list_lay.insertWidget(0, hint)
        self._empty_hint = hint

    def _clear_empty_hint(self):
        if getattr(self, "_empty_hint", None):
            self._list_lay.removeWidget(self._empty_hint)
            self._empty_hint.deleteLater()
            self._empty_hint = None

    def append(self, entry: dict):
        """主线程调用: 追加一条记录(内存 + 文件 + UI)。"""
        self._entries.append(entry)
        self._entries = self._entries[-MAX_ENTRIES:]
        self._save()
        self._clear_empty_hint()
        self._add_card(entry)
        # 滚到底部
        bar = self.scroll.verticalScrollBar()
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))

    def _add_card(self, entry: dict):
        original = str(entry.get("original", ""))
        final = str(entry.get("final", ""))
        polished = bool(entry.get("polished", False))
        ts = str(entry.get("ts", ""))
        card = _ClickCard(self._container)
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        card.setToolTip(tr("点击复制到剪贴板"))
        card.clicked.connect(lambda c=card, f=final or original: self._copy_entry(c, f))
        v = QVBoxLayout(card)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(4)
        head = CaptionLabel(ts)
        head.setStyleSheet("color: #909399;")
        v.addWidget(head)
        if polished and final != original:
            if original:
                orig_lb = CaptionLabel(tr("原: {original}", original=original))
                orig_lb.setWordWrap(True)
                orig_lb.setStyleSheet("color: #a0a4ab;")
                v.addWidget(orig_lb)
            fin_lb = StrongBodyLabel(f"AI: {final}")
            fin_lb.setWordWrap(True)
            v.addWidget(fin_lb)
        else:
            text = final or original
            lb = BodyLabel(text)
            lb.setWordWrap(True)
            v.addWidget(lb)
            if polished:
                tag = CaptionLabel(tr("AI 润色无变化"))
                tag.setStyleSheet("color: #909399;")
                v.addWidget(tag)
        # 插到 stretch 前面
        self._list_lay.insertWidget(self._list_lay.count() - 1, card)

    def _copy_entry(self, card, text):
        """点击卡片: 复制该条最终输出到剪贴板。"""
        from PySide6.QtWidgets import QApplication
        try:
            QApplication.clipboard().setText(text)
        except Exception as e:
            log(f"[chatlog] 复制失败: {e}")
            return
        InfoBar.success(tr("已复制"), text[:30], parent=self,
                        position=InfoBarPosition.TOP, duration=2000)
