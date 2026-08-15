"""VRChat 运行模式检测。
规则: VRChat 进程在跑 + SteamVR(vrserver) 在跑 -> VR 模式
      VRChat 进程在跑 + SteamVR 没跑  -> 桌面模式
      VRChat 没跑                     -> 未运行
"""
import subprocess
import time


# 无窗口运行子进程(pythonw 下不闪 cmd 窗口)
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


def _has_process(name: str) -> bool:
    """tasklist 查询(短超时 3s)。注意: 只能在后线程调用 —— 主线程绝不起 subprocess
    (实测 tasklist 偶发卡死会导致主线程 communicate 卡住 → AppHang)。"""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {name}.exe"],
            capture_output=True, text=True, timeout=3, creationflags=_NO_WINDOW,
        ).stdout
        return f"{name}.exe" in out
    except Exception:
        return False


# tasklist 查询缓存: 高频/多线程调用时不能每次起子进程(几十 ms 开销 + 卡死风险)
_cache = {}  # name -> (ts, bool)
_CACHE_SEC = 1.5


def _has_process_cached(name: str) -> bool:
    global _cache
    now = time.time()
    hit = _cache.get(name)
    if hit and now - hit[0] < _CACHE_SEC:
        return hit[1]
    ok = _has_process(name)
    _cache[name] = (now, ok)
    return ok


def steamvr_running() -> bool:
    """SteamVR(vrserver) 是否在运行(带 1.5s 缓存)。"""
    return _has_process_cached("vrserver")


def vrc_status() -> str:
    """返回: 未运行 / 桌面模式运行中 / VR 模式运行中(SteamVR)(带缓存)。"""
    if not _has_process_cached("VRChat"):
        return "未运行"
    if _has_process_cached("vrserver"):
        return "VR 模式运行中 (SteamVR)"
    return "桌面模式运行中"


# vrc_ok 的缓存: tasklist 子进程有几十毫秒开销, 触发链路高频调用时不能每次起进程
_ok_state = True
_ok_ts = 0.0


def vrc_ok(cache_sec: float = 5.0) -> bool:
    """VRChat 是否在运行(带缓存, 默认 5 秒内不重复探测; 触发链路绝不能因 tasklist 卡死而延迟按下)。"""
    global _ok_state, _ok_ts
    now = time.time()
    if now - _ok_ts > cache_sec:
        _ok_state = vrc_status() != "未运行"
        _ok_ts = now
    return _ok_state
