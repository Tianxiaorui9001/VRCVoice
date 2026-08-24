"""VR 控制器触发: 轮询 HoldToTalk 动作, 握把按住 = 开始识别, 松开 = 延迟结束。

语义与 PC 热键一致: 松开后进入延迟窗口, 窗口内再次按下则无缝继续(取消待定的结束)。
"""
import threading
import time

from .log import log


class VRTriggerPoller(threading.Thread):
    def __init__(self, vr, on_start, on_stop, release_delay: float,
                 interval: float = 0.08, heartbeat_enabled=None,
                 toggle: bool = False, is_active=None):
        super().__init__(daemon=True, name="vr-trigger")
        self._vr = vr
        self._on_start = on_start
        self._on_stop = on_stop
        self._delay = float(release_delay or 0)
        self._interval = interval
        # True=按一下开始/再按一下发送, 松开不结束; 支持 callable(设置改动即时生效)
        self._toggle_fn = toggle if callable(toggle) else (lambda: bool(toggle))
        self._is_active = is_active or (lambda: False)  # controller.is_recording
        self._held = False
        self._timer = None
        self._stop_evt = threading.Event()
        # 心跳日志开关: None = 默认开(兼容旧调用); 传 callable 则每次心跳前求值, 改设置即时生效
        self._heartbeat_enabled = heartbeat_enabled or (lambda: True)

    def run(self):
        last_beat = 0.0
        while not self._stop_evt.is_set():
            old_held = self._held
            d = getattr(self._vr, "last_poll", {})
            try:
                pressed = self._vr.is_hold_talk_pressed()
            except Exception as e:
                log(f"[vr-trigger] 轮询异常: {e}")
                pressed = False
            if pressed and not self._held:
                self._held = True
                self._cancel_pending()  # 延迟窗口内重新按下: 无缝继续
                if self._toggle_fn():
                    # 按一下切换: 识别中→停止发送; 空闲→开始识别
                    try:
                        active = bool(self._is_active())
                    except Exception as e:
                        log(f"[vr-trigger] 状态查询异常: {e}")
                        active = False
                    log(f"[vr-trigger] 按下(toggle) active={active} | ch={d.get('ch')} "
                        f"controllers={d.get('controllers')} "
                        f"bActive={d.get('bActive')} bState={d.get('bState')} "
                        f"maskL={d.get('maskL')} maskR={d.get('maskR')} "
                        f"hand={d.get('hand')} err={d.get('err')}")
                    try:
                        if active:
                            self._on_stop()
                        else:
                            self._on_start()
                    except Exception as e:
                        log(f"[vr-trigger] 切换动作异常: {e}")
                else:
                    log(f"[vr-trigger] 按下 | ch={d.get('ch')} bActive={d.get('bActive')} "
                        f"controllers={d.get('controllers')} "
                        f"bState={d.get('bState')} maskL={d.get('maskL')} maskR={d.get('maskR')} "
                        f"hand={d.get('hand')} err={d.get('err')}")
                    try:
                        self._on_start()
                    except Exception as e:
                        log(f"[vr-trigger] on_start 异常: {e}")
            elif not pressed and self._held:
                self._held = False
                if self._toggle_fn():
                    # 切换模式: 松开不结束(靠再次按下或静音自动停止)
                    log(f"[vr-trigger] 松开(toggle, 不结束) | ch={d.get('ch')} "
                        f"controllers={d.get('controllers')} "
                        f"bActive={d.get('bActive')} bState={d.get('bState')} "
                        f"maskL={d.get('maskL')} maskR={d.get('maskR')} "
                        f"hand={d.get('hand')} err={d.get('err')}")
                else:
                    log(f"[vr-trigger] 松开 | ch={d.get('ch')} bActive={d.get('bActive')} "
                        f"controllers={d.get('controllers')} "
                        f"bState={d.get('bState')} maskL={d.get('maskL')} maskR={d.get('maskR')} "
                        f"hand={d.get('hand')} err={d.get('err')}")
                    if self._delay > 0:
                        self._timer = threading.Timer(self._delay, self._fire_stop)
                        self._timer.daemon = True
                        self._timer.start()
                    else:
                        self._fire_stop()
            now = time.time()
            flipped = (pressed != old_held)
            # 心跳必打(不再被 flipped 抑制): 挂起时只要 poller 活着日志就持续,
            # 附 held/flipped 状态供诊断; 诊断价值 > 刷屏成本(每 3s 一行)
            if now - last_beat >= 3.0:
                last_beat = now
                if self._heartbeat_enabled():
                    hint = " [设备未激活/休眠, 晃动手柄唤醒]" if not d.get("bActive") else ""
                    log(f"[vr-trigger] 心跳 ch={d.get('ch')} bActive={d.get('bActive')} "
                        f"controllers={d.get('controllers')} "
                        f"bState={d.get('bState')} maskL={d.get('maskL')} maskR={d.get('maskR')} "
                        f"hand={d.get('hand')} err={d.get('err')} held={self._held} flipped={flipped} "
                        f"mode={'toggle' if self._toggle_fn() else 'hold'}{hint}")
            time.sleep(self._interval)

    def _fire_stop(self):
        self._timer = None
        try:
            self._on_stop()
        except Exception:
            pass

    def _cancel_pending(self):
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def stop(self):
        self._stop_evt.set()
        self._cancel_pending()
