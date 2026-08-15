"""开机自启: 写 HKCU Run 注册表。"""
import os
import sys
import winreg

APP_NAME = "VRCVoice"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pyw = os.path.join(base, ".venv", "Scripts", "pythonw.exe")
    main = os.path.join(base, "main.py")
    return f'"{pyw}" "{main}"'


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except OSError:
        return False


def set_enabled(on: bool):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, _command())
            else:
                try:
                    winreg.DeleteValue(k, APP_NAME)
                except OSError:
                    pass
    except Exception as e:
        print(f"[autostart] 设置失败: {e}")
