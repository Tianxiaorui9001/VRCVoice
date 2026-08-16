"""更新检查 + 下载: 查询 GitHub 最新 release, 比对版本, 软件内下载安装包。

- 本地版本: 读取应用根目录 VERSION 文件(构建时由 build_dist.ps1 复制)。
- 远端信息: GET api.github.com/repos/Tianxiaorui9001/VRCVoice/releases/latest。
- 下载: release 资产 zip, stream 分块写文件, 支持进度回调与取消。
- 校验: 资产 digest(sha256) 校验, 无 digest 则跳过。
- 网络策略: 直连优先(不走系统代理), 网络异常再走系统代理——部分用户代理出口 IP
  被 GitHub 限流(403)而直连正常; 反过来也有用户直连被墙必须走代理。
"""
import hashlib
import os
import re
import threading
import time
from urllib.parse import urlparse

import requests

from .log import app_base_dir, data_dir

REPO = "Tianxiaorui9001/VRCVoice"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASE_URL = f"https://github.com/{REPO}/releases/latest"
_HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "VRCVoice"}
_TIMEOUT = 8.0
_DL_TIMEOUT = 30.0          # 单块下载超时(秒)
_CHUNK = 256 * 1024         # 256 KB 分块(进度更细腻)
_PROBE_BYTES = 3 << 20      # 测速窗口(直连前 3MB)
_MIN_SPEED = 200 * 1024     # 直连最低速率 200KB/s, 低于则切代理

_RE = re.compile(r"\d+\.\d+\.\d+")


def local_version() -> str:
    """读取 VERSION 文件; 找不到时返回 '0.0.0'。"""
    for base in (app_base_dir(), os.getcwd()):
        p = os.path.join(base, "VERSION")
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    v = f.read().strip()
                if _RE.fullmatch(v):
                    return v
            except OSError:
                pass
    return "0.0.0"


def _ver_tuple(v: str):
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return (0, 0, 0)


def _request(url, timeout, stream=False):
    """双通道请求: 先直连(不走系统代理), 网络异常再走系统代理。
    私有/本机地址永远直连(代理出口可能无法访问, 也无需代理)。"""
    try:
        s = requests.Session()
        s.trust_env = False  # 不走系统代理, 直连
        return s.get(url, timeout=timeout, headers=_HEADERS, stream=stream)
    except Exception:
        host = (urlparse(url).hostname or "").lower()
        if (host == "localhost" or host.endswith(".local")
                or host.startswith("127.") or host.startswith("10.")
                or host.startswith("192.168.") or host.startswith("169.254.")):
            raise
        return requests.get(url, timeout=timeout, headers=_HEADERS, stream=stream)


def check_latest(timeout: float = _TIMEOUT, force: bool = False):
    """检查最新版本。

    返回 (latest, local, has_update, err, asset):
      latest      远端最新版本号, 检查失败时为 None
      local       本地版本号
      has_update  latest > local (force=True 时已是最新也返回 True)
      err         空串=成功; 非空=失败原因
      asset       最新版 zip 资产信息 dict(name/url/size/digest), 失败或无 zip 时为 None
    """
    local = local_version()
    try:
        r = _request(API_URL, timeout)
        if r.status_code != 200:
            return (None, local, False, f"HTTP {r.status_code}", None)
        data = r.json()
        tag = (data.get("tag_name") or "")
        m = _RE.search(tag)
        if not m:
            return (None, local, False, "bad-tag", None)
        latest = m.group(0)
        asset = None
        for a in data.get("assets") or []:
            if (a.get("name") or "").endswith(".zip"):
                asset = {
                    "name": a.get("name"),
                    "url": a.get("browser_download_url"),
                    "size": a.get("size") or 0,
                    "digest": a.get("digest") or "",
                }
                break
        has_update = _ver_tuple(latest) > _ver_tuple(local) or force
        return (latest, local, has_update, "", asset)
    except Exception as e:
        return (None, local, False, str(e), None)


def download_release(asset, dest_path, progress=None, cancel=None):
    """下载 release 资产到 dest_path。

    progress(done_bytes, total_bytes) 可选回调, 每块一次(后台线程调用)。
    cancel: threading.Event, 置位后尽快中断(删除半成品)。
    返回 (ok, err): ok=True 成功; err 为错误描述(下载失败/校验失败/已取消)。
    """
    url = (asset or {}).get("url")
    if not url:
        return (False, "no-url")
    total = asset.get("size") or 0
    tmp = dest_path + ".part"
    # 取消即时中断: 后台线程轮询 cancel, 置位后立刻 close 响应,
    # 正在阻塞的 read 会抛异常, 由 except 分支识别为 cancelled
    cur = {}
    stop_watch = threading.Event()
    if cancel is not None:
        def _watch():
            while not stop_watch.is_set() and not cancel.is_set():
                time.sleep(0.15)
            if cancel.is_set():
                try:
                    cur["r"].close()
                except Exception:
                    pass
        threading.Thread(target=_watch, daemon=True).start()
    try:
        # 通道: 0=直连(测速), 1=代理(系统代理)
        for attempt in (0, 1):
            try:
                if attempt == 0:
                    s = requests.Session()
                    s.trust_env = False  # 不走系统代理, 直连
                    r = s.get(url, timeout=_DL_TIMEOUT, headers=_HEADERS, stream=True)
                else:
                    r = requests.get(url, timeout=_DL_TIMEOUT, headers=_HEADERS, stream=True)
                cur["r"] = r
                if r.status_code != 200:
                    return (False, f"HTTP {r.status_code}")
                done = 0
                t0 = time.time()
                slow = False
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(chunk_size=_CHUNK):
                        if cancel is not None and cancel.is_set():
                            r.close()
                            _remove(tmp)
                            return (False, "cancelled")
                        if chunk:
                            f.write(chunk)
                            done += len(chunk)
                            if progress:
                                progress(done, total)
                            # 直连测速: 窗口内速率过低 -> 丢弃切代理重下
                            if attempt == 0 and done <= _PROBE_BYTES:
                                el = time.time() - t0
                                if el > 1.5 and done / el < _MIN_SPEED:
                                    slow = True
                                    break
                r.close()
                if not slow:
                    break
                _remove(tmp)  # 直连太慢, 半成品作废, 走代理通道
            except Exception:
                _remove(tmp)
                if cancel is not None and cancel.is_set():
                    return (False, "cancelled")
                if attempt == 1:
                    raise
        try:
            # 校验 sha256 (digest 形如 "sha256:xxxx")
            digest = asset.get("digest") or ""
            if digest:
                algo, _, want = digest.partition(":")
                if algo == "sha256" and len(want) == 64:
                    h = hashlib.sha256()
                    with open(tmp, "rb") as f:
                        for c in iter(lambda: f.read(_CHUNK), b""):
                            h.update(c)
                    if h.hexdigest() != want.lower():
                        _remove(tmp)
                        return (False, "sha256-mismatch")
            os.replace(tmp, dest_path)
            return (True, "")
        except Exception as e:
            _remove(tmp)
            return (False, str(e))
    finally:
        stop_watch.set()
    

def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def update_dir() -> str:
    """更新包存放目录(数据目录下 update/)。"""
    d = os.path.join(data_dir(), "update")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d
