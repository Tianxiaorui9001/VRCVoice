"""VRCVoice 入口。
用法: python main.py
"""
import os
import sys
import threading
import time

# 允许直接运行 main.py 时找到 app 包(打包后由 PyInstaller 处理)
if not getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# PySide6 6.11's Shiboken import hook can inspect six's meta-path importer.
# PyInstaller's frozen importer expects loaders it inspects to expose `_path`,
# while six's importer does not. Add the inert attribute before pynput imports
# six.moves, avoiding a startup crash without changing either dependency.
import six
for _finder in sys.meta_path:
    if type(_finder).__name__ == "_SixMetaPathImporter" and not hasattr(_finder, "_path"):
        _finder._path = []

# pynput/six must be imported before PySide6. With PySide6 6.11, importing
# it afterwards can make Shiboken inspect six's meta-path importer and crash
# during startup. Importing our hotkey module first avoids that upstream
# import-order incompatibility for both source and frozen builds.
from app.hotkey import HotkeyListener

from PySide6.QtWidgets import QApplication, QSystemTrayIcon
from PySide6.QtGui import QIcon

from app.settings import Settings
from app.controller import RecognitionController
from app.log import log, LOG_PATH, data_dir
from app.gui.main_window import MainWindow
from app import crash_dialog
from app.i18n import tr, L

import qfluentwidgets
from qfluentwidgets import FluentIcon


class TrayIcon:
    """系统托盘: FluentDesign 圆角菜单。显示窗口 / 快捷开关(润色/翻译) / 重启 / 退出。"""

    def __init__(self, app: QApplication, window: MainWindow, controller: RecognitionController,
                 settings: Settings):
        from PySide6.QtWidgets import QSystemTrayIcon
        from qfluentwidgets import CheckableSystemTrayMenu, Action
        self.app = app
        self.window = window
        self.controller = controller
        self.settings = settings
        self.tray = QSystemTrayIcon(FluentIcon.MICROPHONE.icon(), app)
        self.tray.setToolTip(tr("VRCVoice - 按住说话"))

        # 系统托盘必须用 CheckableSystemTrayMenu: 普通 RoundMenu 在托盘菜单里勾选状态不渲染
        menu = CheckableSystemTrayMenu(parent=window)
        act_show = Action(FluentIcon.PLAY_SOLID, tr("显示窗口"), menu)
        act_show.triggered.connect(lambda: (window.show(), window.raise_()))

        act_polish = Action(FluentIcon.EDIT, tr("AI 润色"), menu, checkable=True,
                            checked=bool(settings.get("polish", "enabled")))
        act_polish.triggered.connect(self._toggle_polish)
        self._polish_action = act_polish
        act_translate = Action(FluentIcon.LANGUAGE, tr("AI 翻译"), menu, checkable=True,
                               checked=bool(settings.get("translate", "enabled")))
        act_translate.triggered.connect(self._toggle_translate)
        self._translate_action = act_translate

        act_restart = Action(FluentIcon.SYNC, tr("重启软件"), menu)
        act_restart.triggered.connect(self._restart)
        act_quit = Action(FluentIcon.CLOSE, tr("退出"), menu)
        act_quit.triggered.connect(app.quit)

        menu.addAction(act_show)
        menu.addSeparator()
        menu.addAction(act_polish)
        menu.addAction(act_translate)
        menu.addSeparator()
        menu.addAction(act_restart)
        menu.addAction(act_quit)
        menu.aboutToShow.connect(self._sync_checks)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_activated)
        self.tray.show()

    def _sync_checks(self):
        """打开菜单前从设置同步勾选状态(设置页可能改过)。"""
        self._polish_action.setChecked(bool(self.settings.get("polish", "enabled")))
        self._translate_action.setChecked(bool(self.settings.get("translate", "enabled")))

    def _toggle_polish(self, checked: bool):
        self.settings.set("polish", "enabled", bool(checked))
        self.settings.save()
        # 设置页开关/横幅同步(托盘勾选不能只在托盘里生效, 界面要一致)
        try:
            self.window.sync_polish_from_settings()
        except Exception:
            pass
        self.tray.showMessage(
            tr("AI 润色"), tr("已开启") if checked else tr("已关闭"),
            QSystemTrayIcon.MessageIcon.Information, 1500)

    def _toggle_translate(self, checked: bool):
        if checked:
            ok, tip = self.controller.translate_config_ready()
            if not ok:
                self._translate_action.setChecked(False)
                self.tray.showMessage(
                    tr("翻译配置不完整"), tip,
                    QSystemTrayIcon.MessageIcon.Warning, 4000)
                return
        self.settings.set("translate", "enabled", bool(checked))
        self.settings.save()
        try:
            self.window.sync_translate_from_settings()
        except Exception:
            pass
        self.tray.showMessage(
            tr("AI 翻译"), tr("已开启") if checked else tr("已关闭"),
            QSystemTrayIcon.MessageIcon.Information, 1500)

    def _restart(self):
        """重启软件: 后台拉起新实例, 自己退出。"""
        import subprocess, sys, os
        base = os.path.dirname(os.path.abspath(__file__))
        if getattr(sys, "frozen", False):
            args = [sys.executable]
            cwd = base
        else:
            args = [os.path.join(base, ".venv", "Scripts", "pythonw.exe"),
                    os.path.join(base, "main.py")]
            cwd = base
        try:
            subprocess.Popen(args, cwd=cwd, creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception:
            pass
        self.app.quit()

    def _on_activated(self, reason):
        if reason == 3:  # DoubleClick
            self.window.show()
            self.window.raise_()


def main():
    # 单实例保护: 重复启动直接退出, 防止多实例抢配置/抢热键/重复发送
    from PySide6.QtCore import QLockFile
    _lock = QLockFile(os.path.join(data_dir(), "vrcvoice.lock"))
    _lock.setStaleLockTime(15000)  # 崩溃残留的锁 15 秒后视为失效, 允许接管
    if not _lock.tryLock(200):
        log("[main] 已有 VRCVoice 实例在运行, 本次启动退出")
        return

    # 启动时重置日志文件, 每次运行只保留本次的日志, 方便定位问题
    # (放在 Settings 之前, 保证配置迁移等早期日志不被清掉)
    try:
        with open(LOG_PATH, "w", encoding="utf-8"):
            pass
    except OSError:
        pass

    settings = Settings().load()

    from app.i18n import L
    L.lang = settings.get("general", "ui_lang")
    L.reload()

    log(f"[main] VRCVoice 启动 pid={os.getpid()}")

    app = crash_dialog.SafeApp(sys.argv)
    app.setApplicationName("VRCVoice")
    app.setQuitOnLastWindowClosed(False)
    # 全局异常钩子: 主线程/后台线程/Qt 槽异常都弹窗, 提供退出或重启
    crash_dialog.install()

    # 主题
    theme = settings.get("general", "theme")
    try:
        if theme == "light":
            qfluentwidgets.setTheme(qfluentwidgets.Theme.LIGHT)
        elif theme == "dark":
            qfluentwidgets.setTheme(qfluentwidgets.Theme.DARK)
        else:
            qfluentwidgets.setTheme(qfluentwidgets.Theme.AUTO)
    except Exception:
        pass

    controller = RecognitionController(settings)

    # 后台预加载 ASR 模型: 首次触发不再卡顿; 失败先记日志, 触发时会有明确错误提示
    def _preload_asr():
        try:
            controller.init_asr()
            log("[asr] 模型已预加载")
        except Exception as e:
            log(f"[asr] 模型预加载失败: {e}")

    threading.Thread(target=_preload_asr, daemon=True).start()

    # 热键监听(PC), 设置里改键后即时重建, 无需重启
    hotkey_holder = {"listener": None}

    def rebuild_hotkey():
        h = hotkey_holder["listener"]
        if h is not None:
            h.stop()
        key = settings.get("trigger", "pc_hotkey")
        if not key:
            hotkey_holder["listener"] = None
            return
        try:
            h = HotkeyListener(
                key, settings.get("trigger", "mode"),
                on_start=controller.start, on_stop=controller.stop,
                release_delay=settings.get("trigger", "release_delay"))
            h.start()
            hotkey_holder["listener"] = h
            log(f"[hotkey] 热键已更新: {key}")
        except Exception as e:
            log(f"[hotkey] 热键启动失败: {e}")

    rebuild_hotkey()

    window = MainWindow(settings, controller, on_hotkey_changed=rebuild_hotkey)
    window.tray_visible = bool(settings.get("general", "tray_enabled"))

    # 桌面悬浮窗: 识别状态提醒(键盘模式没有 VRChat 正在输入提示, 靠它)
    desktop_ov = None
    if settings.get("output", "desktop_overlay"):
        from app.desktop_overlay import DesktopOverlay
        desktop_ov = DesktopOverlay(settings=settings)
        o_state, o_partial, o_finished, o_polish = (
            controller.on_state_changed, controller.on_partial,
            controller.on_finished, controller.on_polish)

        def d_state(rec):
            if rec:
                desktop_ov.show_recording()

        def d_partial(text):
            if text.strip():
                desktop_ov.show_recording(text)

        def d_polish(polishing, text):
            if polishing:
                desktop_ov.show_polishing(text)

        def d_finished(text):
            if not text.strip():
                # 没识别到内容: 不是"已发送", 按问题样式(红字, 无绿边)
                desktop_ov.show_result(
                    settings.get("output", "not_heard_text"),
                    error=True, duration=2000)
            elif text.startswith("["):  # [错误] 按错误样式提醒
                desktop_ov.show_result(text, error=True)
            else:
                desktop_ov.show_result(text)

        # 链式包装: 保留 GUI 已有回调, 同时转发给悬浮窗
        controller.on_state_changed = lambda b: (
            o_state(b) if o_state else None, d_state(b))
        controller.on_partial = lambda t: (
            o_partial(t) if o_partial else None, d_partial(t))
        controller.on_finished = lambda t: (
            o_finished(t) if o_finished else None, d_finished(t))
        controller.on_polish = lambda b, t: (
            o_polish(b, t) if o_polish else None, d_polish(b, t))
        log("[desktop] 桌面悬浮窗已启用")

    # 托盘
    tray = None
    if settings.get("general", "tray_enabled"):
        tray = TrayIcon(app, window, controller, settings)
        window.tray_visible = True

    if not settings.get("general", "start_minimized"):
        window.show()
    else:
        window.hide()

    # VR 适配: 控制器触发(HoldToTalk) + 状态悬浮窗
    # 不会自动启动 SteamVR; 若启动时 SteamVR 未运行, 后台每 5 秒检测一次,
    # 检测到运行后自动初始化 VR(不需要重启应用)
    vr_state = {"vr": None, "overlay": None, "poller": None, "hide_timer": None, "last_result_ts": 0.0}
    vr_wanted = settings.get("trigger", "vr_enabled") or settings.get("vr_overlay", "enabled")
    if vr_wanted:
        import subprocess
        from PySide6.QtCore import QObject, QTimer, Signal
        from app.vr_input import VRInput
        from app.vr_overlay import VROverlay
        from app.vr_trigger import VRTriggerPoller

        class OverlayBridge(QObject):
            sig = Signal(str, str)  # state, text

        bridge = OverlayBridge()

        def _cancel_hide():
            """取消挂起的隐藏定时(防残留 timer 在识别中/润色中触发)。"""
            t = vr_state["hide_timer"]
            if t is not None:
                t.stop()
                vr_state["hide_timer"] = None

        def _schedule_hide(sec):
            _cancel_hide()
            ah = int(sec or 0)
            if ah <= 0:
                return
            ov = vr_state["overlay"]
            if ov is None or not ov.ok:
                return
            t = QTimer()
            t.setSingleShot(True)
            t.timeout.connect(ov.hide)
            t.start(ah * 1000)
            vr_state["hide_timer"] = t

        def _overlay_slot(state, text):
            ov = vr_state["overlay"]
            if ov is None or not ov.ok:
                return
            if state == "idle":
                # 结果/出错还在显示期(auto_hide 未到)时忽略待机信号:
                # 不让"等待说话…"立刻覆盖刚发出的结果(修复"结果闪一下就没了")
                if time.time() - vr_state["last_result_ts"] < int(
                        settings.get("vr_overlay", "auto_hide_sec") or 0):
                    return
                ov.update("idle", text or settings.get("output", "not_heard_text"))
                _schedule_hide(settings.get("vr_overlay", "idle_hide_sec"))
                return
            if state == "recording":
                # 识别中保持显示: 取消一切残留隐藏定时(修复"识别中突然消失/反复闪")
                _cancel_hide()
            ov.update(state, text)
            if state == "polish":
                # AI 润色中: 保持显示直到润色完成(result 状态会调度隐藏)
                _schedule_hide(0)
                return
            if state in ("result", "error"):
                vr_state["last_result_ts"] = time.time()
                _schedule_hide(settings.get("vr_overlay", "auto_hide_sec"))

        bridge.sig.connect(_overlay_slot)  # 队列连接 -> 主线程渲染

        def _attach_overlay_hooks():
            # 链式包装 GUI 已有回调, 同时转发给悬浮窗
            o_state, o_partial, o_finished, o_polish = (
                controller.on_state_changed, controller.on_partial,
                controller.on_finished, controller.on_polish)

            def state_cb(rec):
                bridge.sig.emit("recording" if rec else "idle", "")

            def partial_cb(text):
                bridge.sig.emit("recording", text)

            def polish_cb(polishing, text):
                bridge.sig.emit("polish" if polishing else "idle", text)

            def finished_cb(text):
                if text.startswith("[错误]"):
                    bridge.sig.emit("error", text)
                    # 出错弹窗告知, 不打断使用(非模态)
                    crash_dialog.show_error(tr("VRCVoice 识别出错"), text)
                elif text:
                    bridge.sig.emit("result", text)
                else:
                    bridge.sig.emit("idle", "")

            controller.on_state_changed = lambda b: (
                o_state(b) if o_state else None, state_cb(b))
            controller.on_partial = lambda t: (
                o_partial(t) if o_partial else None, partial_cb(t))
            controller.on_finished = lambda t: (
                o_finished(t) if o_finished else None, finished_cb(t))
            controller.on_polish = lambda b, t: (
                o_polish(b, t) if o_polish else None, polish_cb(b, t))

        def _vr_init():
            retry_timer.stop()
            vr = VRInput()
            if not vr.init(settings.get("trigger", "vr_action")):
                log(f"[vr] OpenVR 初始化失败: {getattr(vr, '_last_error', '?')}")
                return
            vr_state["vr"] = vr
            log("[vr] OpenVR 就绪, HoldToTalk 动作已加载")
            if settings.get("vr_overlay", "enabled"):
                ov = VROverlay(settings.get("vr_overlay", "width_px"),
                               settings.get("vr_overlay", "height_px"))
                if ov.init(scale=settings.get("vr_overlay", "scale"),
                           x=settings.get("vr_overlay", "x"),
                           y=settings.get("vr_overlay", "y")):
                    vr_state["overlay"] = ov
                    log("[vr] 悬浮窗已创建")
                    _attach_overlay_hooks()
                else:
                    log("[vr] 悬浮窗创建失败, 仅保留控制器触发")
            if settings.get("trigger", "vr_enabled"):
                p = VRTriggerPoller(
                    vr, controller.start, controller.stop,
                    settings.get("trigger", "release_delay"),
                    heartbeat_enabled=lambda: bool(settings.get("debug", "show_heartbeat_log")),
                    toggle=lambda: settings.get("trigger", "mode") == "toggle",
                    is_active=lambda: controller.is_recording)
                p.start()
                vr_state["poller"] = p
                mode_txt = "按一下切换" if settings.get("trigger", "mode") == "toggle" else "按住说话"
                log(f"[vr] VR 触发轮询已启动(模式={mode_txt}, 松开延迟 "
                    f"{settings.get('trigger', 'release_delay')}s)")

        _steamvr_warned = [False]
        _steamvr_checking = [False]  # 防重入(后台查询未完成时跳过本轮)

        def _check_steamvr():
            # 主线程只负责启动后台查询 —— 绝不自己跑 tasklist(实测会卡死主线程 → AppHang)
            if _steamvr_checking[0]:
                return
            _steamvr_checking[0] = True
            threading.Thread(target=_check_steamvr_bg, daemon=True).start()

        def _check_steamvr_bg():
            try:
                flag = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
                try:
                    running = subprocess.run(
                        ["tasklist", "/FI", "IMAGENAME eq vrserver.exe"],
                        capture_output=True, text=True, timeout=3, creationflags=flag).stdout
                except Exception:
                    running = ""
                if "vrserver.exe" in running:
                    log("[vr] 检测到 SteamVR 运行, 初始化 VR 适配")
                    threading.Thread(target=_vr_init, daemon=True).start()
                elif not _steamvr_warned[0]:
                    _steamvr_warned[0] = True
                    log("[vr] SteamVR 未运行, 后台每 5 秒自动检测, 启动后自动接入(不会自动启动 SteamVR)")
            finally:
                _steamvr_checking[0] = False

        retry_timer = QTimer()
        retry_timer.timeout.connect(_check_steamvr)
        retry_timer.start(5000)
        _check_steamvr()  # 立即先查一次

        # ---- VR 看门狗: openvr IPC 挂起时轮询线程会永久卡死(锁超时救不了调用本身), ----
        # 表现=识别停不下来+悬浮窗冻结+随后消失。每 5s 查一次轮询时间戳,
        # 15s 无更新判定挂起 → 后台销毁旧适配并重新初始化。
        def _vr_rebuild():
            try:
                if vr_state["poller"]:
                    try:
                        vr_state["poller"].stop()
                    except Exception:
                        pass
                    vr_state["poller"] = None
                if vr_state["overlay"]:
                    try:
                        vr_state["overlay"].destroy()
                    except Exception:
                        pass
                    vr_state["overlay"] = None
                if vr_state["vr"]:
                    try:
                        vr_state["vr"].shutdown()
                    except Exception:
                        pass
                    vr_state["vr"] = None
            except Exception as e:
                log(f"[vr] 看门狗清理失败: {e}")
            try:
                _vr_init()
                log("[vr] 看门狗: VR 适配已重建")
            except Exception as e:
                log(f"[vr] 看门狗重建失败: {e}")

        def _vr_watchdog():
            vr = vr_state["vr"]
            if vr is None or not getattr(vr, "_ready", False):
                return
            ts = getattr(vr, "_last_poll_ts", 0)
            if ts and time.time() - ts > 15:
                log("[vr] 看门狗: VR 轮询 15s 无更新(疑似 openvr IPC 挂起), 后台重建")
                threading.Thread(target=_vr_rebuild, daemon=True).start()

        wd_timer = QTimer()
        wd_timer.timeout.connect(_vr_watchdog)
        wd_timer.start(5000)

    app.exec()

    if hotkey_holder["listener"]:
        hotkey_holder["listener"].stop()
    if vr_state["poller"]:
        vr_state["poller"].stop()
    if vr_state["overlay"]:
        vr_state["overlay"].destroy()
    if vr_state["vr"]:
        vr_state["vr"].shutdown()
    settings.save()


if __name__ == "__main__":
    main()
