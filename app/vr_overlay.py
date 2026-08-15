"""VR 悬浮窗: 在 SteamVR 里显示识别状态(识别中/结果文本/错误)。

用 openvr 包的 IVROverlay.setOverlayRaw 直接上传 RGBA 纹理, 不需要 D3D/OpenGL。
纹理用 Qt QImage/QPainter 渲染(程序已依赖 Qt, 免加 PIL)。

线程模型(重要): 主线程零 openvr 调用 —— 只渲染 QImage + 提交请求到队列;
后台 worker 线程串行执行所有 openvr 调用(纹理上传/show/hide), 与轮询线程
通过全局 OPENVR_LOCK 互斥(见 vr_input)。这样主线程永不被 openvr 阻塞,
避免 AppHang; 也避免多线程并发进 openvr 死锁。
"""
import threading
import time

from .log import log
from .vr_input import OPENVR_LOCK


# 模块级单例: main.py 创建 overlay 后自动注册, 调试 UI 可随时拿到实例
_active = None


def get_overlay():
    """返回当前 VROverlay 实例(调试/UI 用), 未创建返回 None。"""
    return _active


class VROverlay:
    def __init__(self, width_px: int = 800, height_px: int = 300):
        global _active
        _active = self
        self._overlay = None
        self._handle = None
        self._ready = False
        self._shown = False
        self.width_px = int(width_px or 800)
        self.height_px = int(height_px or 300)
        self._cfg_height = self.height_px  # 配置高度: 单行状态(识别中/润色中)恢复用
        # worker 队列: 主线程只提交请求(渲染/排队), 绝不碰 openvr;
        # worker 线程串行执行所有 openvr 调用(setOverlayRaw / show / hide)
        self._req_lock = threading.Lock()
        self._reqs = []          # [("frame", raw) | ("show",) | ("hide",)]
        self._worker = None
        self._last_partial = None   # partial 文本去重: 相同文本不重传(省上传防闪)
        self._last_show_ts = 0.0    # 最近一次 showOverlay 时间(自愈被 SteamVR 静默隐藏)
        self._last_op_ts = time.time()  # worker 最近完成操作时间(卡死检测)

    @property
    def ok(self) -> bool:
        return self._ready

    def init(self, scale: float = 1.0, x: float = 0.5, y: float = 0.5) -> bool:
        """创建悬浮窗。SteamVR 未运行/接口不可用时返回 False。"""
        try:
            import openvr
            import os
            from openvr import HmdMatrix34_t
            self._overlay = openvr.IVROverlay()
            # key 带 pid: 避免强杀残留的同名 overlay 导致 KeyInUse(无法创建)
            self._handle = self._overlay.createOverlay(
                f"VRCVoiceStatus_{os.getpid()}", "VRCVoice 状态")
            # 不进仪表盘标签页
            self._overlay.setOverlayFlag(
                self._handle, openvr.VROverlayFlags_NoDashboardTab, True)
            # 挂在头显前方 1.5m, 偏移按设置 x/y(0=左下 0.5=中 1=右上)
            mat = HmdMatrix34_t()
            mat.m[0][0] = 1.0
            mat.m[1][1] = 1.0
            mat.m[2][2] = 1.0
            mat.m[0][3] = (float(x) - 0.5) * 1.0   # 左右 ±0.5m
            mat.m[1][3] = (float(y) - 0.5) * 0.6   # 上下 ±0.3m
            mat.m[2][3] = -1.5                      # 前方 1.5m
            self._overlay.setOverlayTransformTrackedDeviceRelative(
                self._handle, 0, mat)  # 0 = HMD
            self._overlay.setOverlayWidthInMeters(
                self._handle, 0.9 * float(scale))
            self._overlay.setOverlayAlpha(self._handle, 0.92)
            self._overlay.hideOverlay(self._handle)
            self._ready = True
            return True
        except Exception as e:
            log(f"[vr] 悬浮窗创建失败: {e}")
            self._ready = False
            return False

    def update(self, state: str, text: str) -> None:
        """主线程调用: 渲染一帧并提交给 worker 异步上传。state: recording / result / error。
        相同 partial 文本跳过(识别中字没变就不重传, 减少闪烁与上传)。
        已去除 1.2s 节流: 识别文本每次变化立即刷新悬浮窗(用户要求), 靠 partial 去重控制上传频率。"""
        if not self._ready or self._overlay is None:
            return
        now = time.time()
        if state == "recording":
            if text == self._last_partial:
                return
            self._last_partial = text
        else:
            self._last_partial = None
        try:
            img = self._render(state, text)
            raw = bytes(img.constBits())
        except Exception as e:
            log(f"[vr] 悬浮窗渲染失败: {e}")
            return
        with self._req_lock:
            # 携带当前纹理尺寸: 渲染在渲染时定高, worker 用同一份尺寸上传, 避免竞态
            self._reqs.append(("frame", raw, self.width_px, self.height_px))
            self._ensure_worker_locked()
            # 卡死检测: worker 活着但超过 10s 没完成任何操作且队列有积压 → 大概率 IPC 挂起
            if (self._worker and self._worker.is_alive()
                    and now - self._last_op_ts > 10 and len(self._reqs) > 1):
                log(f"[vr] 警告: 悬浮窗 worker 疑似卡死(10s 无操作, 队列 {len(self._reqs)}), "
                    f"openvr IPC 可能挂起; 已降低上传频率, 若持续出现请重启应用")

    def _ensure_worker_locked(self):
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(
                target=self._worker_loop, daemon=True, name="vr-overlay-worker")
            self._worker.start()

    def _worker_loop(self):
        """后台线程: 串行执行 openvr 调用, 处理到队列空为止。
        帧请求只保留最新(积压旧帧丢弃, 避免"显示拖拉")。"""
        while True:
            with self._req_lock:
                if not self._reqs:
                    return
                req = self._reqs.pop(0)
                if req[0] == "frame":
                    # 丢弃队列里积压的旧帧, 只留最新的; show/hide 保留顺序
                    self._reqs = [r for r in self._reqs if r[0] != "frame"]
            kind = req[0]
            try:
                # 超时锁: 即使 SteamVR IPC 卡死占锁, worker 也不会永久阻塞, 放弃本次等下次
                if not OPENVR_LOCK.acquire(timeout=2.0):
                    log("[vr] 悬浮窗操作超时(锁忙), 跳过本次")
                    continue
                try:
                    if kind == "frame":
                        import ctypes
                        raw = req[1]
                        fw, fh = req[2], req[3]
                        buf = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
                        self._overlay.setOverlayRaw(
                            self._handle, buf, fw, fh, 4)
                        # 自愈: 距上次 show 超 3s 也重 show(SteamVR 可能静默隐藏 overlay)
                        if (not self._shown) or (time.time() - self._last_show_ts > 3.0):
                            with self._req_lock:
                                hide_pending = bool(self._reqs) and self._reqs[0][0] == "hide"
                            if not hide_pending:
                                self._overlay.showOverlay(self._handle)
                                self._shown = True
                                self._last_show_ts = time.time()
                    elif kind == "show":
                        self._overlay.showOverlay(self._handle)
                        self._shown = True
                    elif kind == "hide":
                        self._overlay.hideOverlay(self._handle)
                        self._shown = False
                finally:
                    OPENVR_LOCK.release()
                self._last_op_ts = time.time()
            except Exception as e:
                log(f"[vr] 悬浮窗操作失败({kind}): {e}")
                self._last_op_ts = time.time()

    def show(self, text: str = "调试显示"):
        """调试用: 提交一帧并显示(等价的 recording 状态)。"""
        self.update("recording", text)

    def hide(self):
        """主线程调用: 丢弃积压请求并提交 hide, worker 执行后彻底隐藏。"""
        if not self._ready:
            return
        with self._req_lock:
            self._reqs.clear()
            self._reqs.append(("hide",))
            self._ensure_worker_locked()

    def status(self) -> dict:
        """调试用: 返回悬浮窗实时状态。超时锁 —— 主线程(QTimer 2s 轮询)调用,
        拿不到锁最多等 50ms 就返回 err=busy, 绝不阻塞主线程(AppHang 防护)。"""
        st = {"ok": self._ready, "shown": self._shown, "handle": self._handle,
              "visible": None, "width_m": None, "alpha": None, "err": None}
        if not self._ready or self._overlay is None:
            return st
        try:
            from .vrc_status import steamvr_running
            if not steamvr_running():
                st["err"] = "SteamVR 未运行"
                return st
            import openvr
            if not OPENVR_LOCK.acquire(timeout=0.05):
                st["err"] = "busy(上传线程持锁)"
                return st
            try:
                r = self._overlay.getOverlayVisibility(self._handle)
                st["visible"] = bool(r[0] if isinstance(r, tuple) else r)
                st["width_m"] = round(self._overlay.getOverlayWidthInMeters(self._handle), 3)
                st["alpha"] = round(self._overlay.getOverlayAlpha(self._handle), 2)
            finally:
                OPENVR_LOCK.release()
        except Exception as e:
            st["err"] = str(e)
        return st

    def destroy(self):
        if self._ready:
            try:
                import openvr
                if OPENVR_LOCK.acquire(timeout=0.2):
                    try:
                        self._overlay.destroyOverlay(self._handle)
                    finally:
                        OPENVR_LOCK.release()
            except Exception:
                pass
            self._ready = False
            self._shown = False
    def _render(self, state: str, text: str):
        """画一帧 RGBA 图。必须主线程(Qt 字体渲染)。
        高度策略: 待机=单行像素级截断, 纹理高度保持配置值;
        识别中/润色中/结果/出错=自动换行, 纹理高度随行数增高(上限 420px),
        超出截断加省略号, 长文本不会画出卡片外。"""
        from PySide6.QtCore import Qt, QRect
        from PySide6.QtGui import (QColor, QFont, QFontMetrics, QImage, QPainter, QPen)
        w = self.width_px
        # ---- 先定正文与高度, 再建画布 ----
        f_body = QFont("Microsoft YaHei", 26)
        fm = QFontMetrics(f_body)
        avail_w = w - 72
        body = text or "等待说话…"
        if state in ("result", "error"):
            # 结果/出错: 动态增高(长结果完整显示), 行数变化才增高
            max_body_h = 420 - 92 - 24
            for _ in range(400):
                r = fm.boundingRect(QRect(36, 92, avail_w, 100000),
                                    Qt.TextFlag.TextWordWrap, body)
                if r.height() <= max_body_h or len(body) <= 1:
                    break
                body = body[:-3].rstrip() + "…"
            r = fm.boundingRect(QRect(36, 92, avail_w, 100000),
                                Qt.TextFlag.TextWordWrap, body)
            text_h = r.height()
            h = 92 + text_h + 24
        else:
            # 待机/识别中/润色中: 固定配置高度 + 自动换行(超高自动裁剪) ——
            # 纹理尺寸恒定, SteamVR 不重排 overlay 不闪; 长文多行显示不省略
            h = self._cfg_height
        self.height_px = h
        img = QImage(w, h, QImage.Format_RGBA8888)
        img.fill(QColor(0, 0, 0, 0))
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        colors = {
            "idle": QColor(150, 155, 165),       # 灰: 待机中
            "recording": QColor(215, 215, 225),  # 白灰, 不刺眼
            "polish": QColor(245, 200, 107),     # 琥珀: AI 润色中
            "result": QColor(120, 200, 130),     # 绿: 已发送
            "error": QColor(235, 90, 90),
        }
        badge = {
            "idle": "○ 待机中",
            "recording": "● 识别中",
            "polish": "✦ AI 润色中",
            "result": "✓ 已发送",
            "error": "● 出错",
        }
        accent = colors.get(state, QColor(120, 120, 130))
        # 背景圆角卡片
        p.setPen(QPen(accent, 3))
        p.setBrush(QColor(14, 16, 20, 230))
        p.drawRoundedRect(2, 2, w - 4, h - 4, 18, 18)
        # 标题
        p.setFont(QFont("Microsoft YaHei", 20, QFont.Bold))
        p.setPen(QColor(225, 225, 235))
        p.drawText(28, 22, w - 260, 44, Qt.AlignLeft | Qt.AlignVCenter, "VRCVoice")
        # 状态徽标
        p.setFont(QFont("Microsoft YaHei", 15, QFont.DemiBold))
        p.setPen(accent)
        p.drawText(w - 232, 22, 200, 44, Qt.AlignRight | Qt.AlignVCenter,
                   badge.get(state, ""))
        # 分隔线
        p.setPen(QColor(70, 75, 85))
        p.drawLine(30, 72, w - 30, 72)
        # 正文
        p.setFont(f_body)
        p.setPen(QColor(242, 242, 247))
        p.drawText(QRect(36, 92, avail_w, h - 116),
                   Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap,
                   body)
        p.end()
        return img
