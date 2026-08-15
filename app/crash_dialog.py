"""全局未捕获异常处理: 弹窗显示错误, 提供 退出 / 重启 选项。

- 主线程异常(sys.excepthook) / 后台线程异常(threading.excepthook) / Qt 槽异常(SafeApp.notify)
  全部捕获 → 主线程弹窗, 展示完整 traceback, 用户可复制, 可选 [退出软件] 或 [重启软件]。
- 业务错误(如 ASR 模型加载失败)可调 show_error() 弹非模态提示, 不打断使用。
"""
import os
import sys
import threading
import traceback

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel,
                               QPushButton, QTextEdit, QVBoxLayout)

_frozen = bool(getattr(sys, "frozen", False))


class _Bridge(QObject):
    sig = Signal(str, str, bool)  # title, body, modal(True=必须选择退出/重启)


_bridge = _Bridge()
_bridge.sig.connect(lambda t, b, m: _show_dialog(t, b, m), Qt.QueuedConnection)

# 防弹窗风暴: 同一时刻只允许一个弹窗
_show_lock = threading.Lock()
_showing = False


def _restart_app():
    """重启软件: frozen 直接再起 exe; 源码用 venv python + main.py。"""
    try:
        from PySide6.QtCore import QProcess
        args = []
        if not _frozen:
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            args = [os.path.join(root, "main.py")]
        # 先启动新实例, 旧实例随后 sys.exit 释放 QLockFile,
        # 新实例 Python 初始化耗时 > 旧实例退出耗时, 锁已释放可正常接管
        QProcess.startDetached(sys.executable, args)
    except Exception:
        pass


def _show_dialog(title: str, body: str, modal: bool):
    global _showing
    app = QApplication.instance()
    if app is None:
        return
    with _show_lock:
        if _showing:
            return
        _showing = True
    try:
        dlg = QDialog()
        dlg.setWindowTitle(title)
        dlg.setMinimumWidth(600)
        dlg.setModal(modal)
        lay = QVBoxLayout(dlg)
        lab = QLabel("发生错误:\n\n" + body[:2000])
        lab.setWordWrap(True)
        lay.addWidget(lab)
        detail = QTextEdit()
        detail.setPlainText(body)
        detail.setReadOnly(True)
        detail.setMaximumHeight(220)
        lay.addWidget(detail)
        btns = QHBoxLayout()
        if modal:
            b_quit = QPushButton("退出软件")
            b_restart = QPushButton("重启软件")
            b_quit.clicked.connect(lambda: (dlg.accept(), sys.exit(1)))
            b_restart.clicked.connect(lambda: (_restart_app(), dlg.accept(), sys.exit(0)))
            btns.addWidget(b_quit)
            btns.addWidget(b_restart)
        else:
            b_ok = QPushButton("知道了")
            b_ok.clicked.connect(dlg.accept)
            btns.addWidget(b_ok)
        lay.addLayout(btns)
        if modal:
            dlg.exec()
        else:
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
    except Exception:
        pass
    finally:
        with _show_lock:
            _showing = False


def _on_exception(exc_type, exc_value, exc_tb):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    body = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    _bridge.sig.emit("VRCVoice 出错", body, True)


def _on_thread_exception(args):
    body = "".join(traceback.format_exception(
        args.exc_type, args.exc_value, args.exc_traceback))
    _bridge.sig.emit("VRCVoice 线程出错", body, True)


def install():
    """安装全局异常钩子(主线程 + 后台线程)。在 QApplication 创建后调用。"""
    sys.excepthook = _on_exception
    threading.excepthook = _on_thread_exception


def show_error(title: str, body: str):
    """业务错误弹窗(非模态, 不打断使用)。"""
    _bridge.sig.emit(title, body, False)


class SafeApp(QApplication):
    """捕获 Qt 事件槽里的 Python 异常(否则会被 Qt 吞掉, 只打印到 stderr)。"""

    def notify(self, receiver, event):
        try:
            return super().notify(receiver, event)
        except Exception:
            body = "".join(traceback.format_exception(*sys.exc_info()))
            _bridge.sig.emit("VRCVoice 界面出错", body, True)
            return False
