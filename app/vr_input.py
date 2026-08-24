"""SteamVR 输入: 通过 OpenVR 官方 Python 绑定(openvr 包)实现 HoldToTalk 动作。

单通道读取: 只走 SteamVR Input 动作(HoldToTalk), 绑定生效(bActive=True)才触发。
不再做旧 API 按钮掩码兜底(通道 B): 掩码在不同驱动上不可靠(如 PICO 摇杆不报掩码),
且与动作通道存在时序竞争, 造成"时灵时不灵"的假象。绑定没生效就是没生效,
心跳日志会明确显示 bActive=False, 便于排查。

注意: SteamVR 对应用绑定状态有缓存, 新增默认绑定后可能需要重启 SteamVR 才生效。
"""
import hashlib
import json
import os
import shutil
import sys
import threading
import time

from .log import app_base_dir, data_dir, log

APP_DIR = app_base_dir()

# 全局 openvr 串行锁: SteamVR 服务端 IPC 并发调用会死锁(实测: 上传线程/主线程/轮询线程
# 同时进 openvr → 全进程卡死, AppHang 后被系统终止)。所有 openvr 调用必须先拿这把锁。
OPENVR_LOCK = threading.Lock()


def _file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _runtime_resources_dir() -> str:
    """manifest/bindings 所在目录。
    frozen: exe 目录可能只读(如 Program Files), 将内置资源同步到 APPDATA 下 VRCVoice/resources;
    源码: 直接用项目 resources。"""
    if getattr(sys, "frozen", False):
        src = os.path.join(APP_DIR, "resources")
        dst = os.path.join(data_dir(), "resources")
        os.makedirs(dst, exist_ok=True)
        if os.path.isdir(src):
            for fn in os.listdir(src):
                if not fn.endswith(".json"):
                    continue
                src_path = os.path.join(src, fn)
                dst_path = os.path.join(dst, fn)
                tmp_path = dst_path + ".tmp"
                try:
                    src_sha = _file_sha256(src_path)
                    dst_sha = _file_sha256(dst_path) if os.path.isfile(dst_path) else None
                    if src_sha != dst_sha:
                        shutil.copy2(src_path, tmp_path)
                        os.replace(tmp_path, dst_path)
                        log(f"[vr] 内置输入资源已更新: {fn} sha256={src_sha[:12]}")
                except Exception as e:
                    log(f"[vr] 内置输入资源更新失败: {fn}: {e}")
                    try:
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)
                    except Exception:
                        pass
        return dst
    return os.path.join(APP_DIR, "resources")


RESOURCES_DIR = _runtime_resources_dir()
MANIFEST_PATH = os.path.join(RESOURCES_DIR, "action_manifest.json")


class VRInput:
    """封装 openvr: 初始化 + 双通道轮询 HoldToTalk。"""

    def __init__(self):
        self._input = None
        self._action_set = 0
        self._action = 0
        self._ready = False
        self._active_set = None   # 缓存 VRActiveActionSet_t, 避免每帧重建
        self._last_error = ""
        self.action_channel_alive = False
        # 最近一次轮询诊断(供日志): 通道 / bActive / bState / 左右手掩码
        self.last_poll = {"ch": "A", "bActive": None, "bState": None,
                          "mask": "-", "maskL": "-", "maskR": "-",
                          "hand": "-", "controllers": "-", "err": None}
        self._last_hold = False  # 上次轮询结果(锁忙时保持, 防误判松开)
        self._last_poll_ts = 0.0  # 最近一次成功轮询时间(看门狗检测 IPC 挂起用)

    def available(self) -> bool:
        """openvr 包可用(自带 DLL), 不代表 SteamVR 在运行。"""
        try:
            import openvr  # noqa: F401
            return True
        except Exception:
            return False

    def init(self, action_name: str = "HoldToTalk") -> bool:
        """初始化 OpenVR + 加载 action manifest。失败时返回 False。"""
        try:
            import openvr
            system = openvr.init(openvr.VRApplication_Overlay)
            self._input = openvr.IVRInput()
            self._input.setActionManifestPath(MANIFEST_PATH)
            self._action_set = self._input.getActionSetHandle("/actions/default")
            self._action = self._input.getActionHandle(
                f"/actions/default/in/{action_name}")
            # 必须传 ctypes 数组(openvr 对 Python list 会创建空 action set 丢弃内容!)
            self._active_sets = (openvr.VRActiveActionSet_t * 1)()
            self._active_sets[0].ulActionSet = self._action_set
            log(f"[vr] Action Manifest 已加载: {MANIFEST_PATH} "
                f"sha256={_file_sha256(MANIFEST_PATH)[:12]}")
            oculus_binding = os.path.join(RESOURCES_DIR, "bindings_oculus.json")
            log(f"[vr] Oculus binding: {oculus_binding} "
                f"sha256={_file_sha256(oculus_binding)[:12]}")
            log(f"[vr] Action handles: set={self._action_set} HoldToTalk={self._action}")
            self._log_runtime_diagnostics(system, openvr)
            self._ready = True
            return True
        except Exception as e:
            try:
                import openvr
                openvr.shutdown()
            except Exception:
                pass
            self._ready = False
            self._last_error = str(e)
            return False

    def _log_runtime_diagnostics(self, system, openvr):
        """记录实际控制器类型和输入配置，便于无设备环境下定位默认绑定未命中。"""
        try:
            log(f"[vr] SteamVR runtime={system.getRuntimeVersion()} application=Overlay")
        except Exception as e:
            log(f"[vr] SteamVR runtime 读取失败: {e}")

        role_names = {
            int(openvr.TrackedControllerRole_LeftHand): "LeftHand",
            int(openvr.TrackedControllerRole_RightHand): "RightHand",
            int(openvr.TrackedControllerRole_OptOut): "OptOut",
            int(openvr.TrackedControllerRole_Treadmill): "Treadmill",
            int(openvr.TrackedControllerRole_Stylus): "Stylus",
        }
        controller_types = []
        for index in range(openvr.k_unMaxTrackedDeviceCount):
            try:
                if not system.isTrackedDeviceConnected(index):
                    continue
                if system.getTrackedDeviceClass(index) != openvr.TrackedDeviceClass_Controller:
                    continue
                role = int(system.getControllerRoleForTrackedDeviceIndex(index))

                def prop(name):
                    try:
                        return system.getStringTrackedDeviceProperty(index, getattr(openvr, name))
                    except Exception as e:
                        return f"<读取失败: {e}>"

                controller_type = prop("Prop_ControllerType_String")
                controller_types.append(controller_type)
                log(f"[vr] 控制器 index={index} role={role_names.get(role, str(role))} "
                    f"controller_type={controller_type} "
                    f"input_profile={prop('Prop_InputProfilePath_String')} "
                    f"model={prop('Prop_ModelNumber_String')} "
                    f"render_model={prop('Prop_RenderModelName_String')}")
            except Exception as e:
                log(f"[vr] 控制器诊断失败 index={index}: {e}")
        self.last_poll["controllers"] = ",".join(dict.fromkeys(controller_types)) or "未检测到控制器"

    def is_hold_talk_pressed(self) -> bool:
        """单通道轮询: 只认 SteamVR Input 动作绑定(bActive=True 且 bState=True)。
        超时锁: 悬浮窗上传线程忙时最多等 30ms; 拿不到锁时**保持上次状态**(绝不
        误判为"松开"导致识别被切断), last_poll 打 err=busy 供心跳日志诊断。"""
        if not self._ready or self._input is None:
            return False
        if not OPENVR_LOCK.acquire(timeout=0.03):
            self.last_poll.update(err="busy(上传线程持锁)")
            return self._last_hold
        try:
            import openvr
            self._input.updateActionState(self._active_sets)
            data = self._input.getDigitalActionData(self._action, 0)
            self._last_poll_ts = time.time()
            self.last_poll.update(ch="A", bActive=bool(data.bActive),
                                  bState=bool(data.bState),
                                  mask="-", maskL="-", maskR="-",
                                  hand="-", err=None)
            # 绑定未生效(bActive=False)时不触发, 心跳日志会提示
            self._last_hold = bool(data.bActive and data.bState)
            return self._last_hold
        except Exception as e:
            self.last_poll.update(err=str(e))
            return self._last_hold
        finally:
            OPENVR_LOCK.release()

    def shutdown(self):
        """关闭时调用: 超时锁, 不等持锁线程(避免主线程在退出时被拖死)。"""
        try:
            import openvr
            if OPENVR_LOCK.acquire(timeout=0.2):
                try:
                    openvr.shutdown()
                finally:
                    OPENVR_LOCK.release()
        except Exception:
            pass
        self._ready = False


def make_action_manifest():
    """生成 action_manifest.json(含主流手柄 + Pico 绑定)。"""
    manifest = {
        "default_bindings": [
            {"binding_url": "bindings_pico.json", "controller_type": "pico_controller"},
            {"binding_url": "bindings_default.json", "controller_type": "knuckles"},
            {"binding_url": "bindings_oculus.json", "controller_type": "oculus_touch"},
            {"binding_url": "bindings_wmr.json", "controller_type": "holographic_controller"},
        ],
        "actions": [
            {
                "name": "/actions/default/in/HoldToTalk",
                "type": "boolean",
                "requirement": "optional",
            }
        ],
        "action_sets": [{"name": "/actions/default", "usage": "single"}],
        "localization": [
            {
                "language_tag": "zh_CN",
                "actions": [
                    {"name": "/actions/default/in/HoldToTalk", "string": "按住说话"}
                ],
                "action_sets": [
                    {"name": "/actions/default", "string": "语音输入"}
                ],
            }
        ],
    }
    os.makedirs(os.path.dirname(MANIFEST_PATH), exist_ok=True)
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return MANIFEST_PATH
