"""简单文件日志: GUI 跑在 pythonw 下没有控制台, 所有诊断信息写文件。"""
import os
import sys
import threading
from datetime import datetime


def app_base_dir() -> str:
    """应用根目录: 源码运行时=项目目录, 打包后=exe 所在目录。
    models / resources(只读) 基于此路径。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_dir() -> str:
    """可写数据目录: config.json / vrcvoice.log / vrcvoice.lock 放这里。
    打包后=%APPDATA% 下 VRCVoice 目录(任何位置安装都可写), 源码运行时=项目目录。"""
    if getattr(sys, "frozen", False):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        d = os.path.join(base, "VRCVoice")
    else:
        d = app_base_dir()
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


LOG_PATH = os.path.join(data_dir(), "vrcvoice.log")
_lock = threading.Lock()


def log(msg: str):
    try:
        with _lock:
            os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass
