"""更新检查: 查询 GitHub 最新 release, 与本地版本比较。

- 本地版本: 读取应用根目录 VERSION 文件(构建时由 build_dist.ps1 复制)。
- 远端版本: GET api.github.com/repos/Tianxiaorui9001/VRCVoice/releases/latest。
- 用法: 在后台线程调用 check_latest(), 用 Signal 回主线程更新 UI。
"""
import os
import re

import requests

from .log import app_base_dir

REPO = "Tianxiaorui9001/VRCVoice"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASE_URL = f"https://github.com/{REPO}/releases/latest"
_TIMEOUT = 8.0

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


def _request(url, timeout):
    """优先直连(不走系统代理), 直连网络失败再试系统代理。
    原因: 部分用户开着系统代理, 代理出口 IP 常被 GitHub API 限流(403),
    而直连反而正常; 反过来也有用户直连被墙、必须走代理。两个通道都试一遍最稳。"""
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "VRCVoice"}
    try:
        s = requests.Session()
        s.trust_env = False  # 不走系统代理, 直连
        return s.get(url, timeout=timeout, headers=headers)
    except Exception:
        return requests.get(url, timeout=timeout, headers=headers)


def check_latest(timeout: float = _TIMEOUT):
    """检查最新版本。

    返回 (latest, local, has_update, error):
      latest      远端最新版本号, 检查失败时为 None
      local       本地版本号
      has_update  latest > local
      error       空串=成功; 非空=失败原因(网络/HTTP 状态/解析失败)
    """
    local = local_version()
    try:
        r = _request(API_URL, timeout)
        if r.status_code != 200:
            return (None, local, False, f"HTTP {r.status_code}")
        tag = (r.json().get("tag_name") or "")
        m = _RE.search(tag)
        if not m:
            return (None, local, False, "bad-tag")
        latest = m.group(0)
        return (latest, local, _ver_tuple(latest) > _ver_tuple(local), "")
    except Exception as e:  # 网络错误/超时/JSON 解析等
        return (None, local, False, str(e))
