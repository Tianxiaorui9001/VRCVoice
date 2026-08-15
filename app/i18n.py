# -*- coding: utf-8 -*-
"""轻量 i18n: 主目录 Language/<lang>.json 语言文件。

- tr("界面原文", 占位=值) 取文本; 缺 key 回退 zh-CN, 再回退 key 本身(不显示空)。
- 语言文件 key = 界面原文, 值 = 该语言下的显示文本(zh-CN 值与 key 相同,
  用户直接改值即可改描述; 含 {占位} 的 key 值里保留同名占位)。
- 切换语言后需重启应用生效(界面构建时取文本)。
"""
import json
import os

_LANG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Language")

_AVAILABLE = ["zh-CN", "zh-TW", "en-US", "ja"]


class I18n:
    def __init__(self, lang_dir=_LANG_DIR, lang="zh-CN"):
        self.lang_dir = lang_dir
        self.lang = lang if lang in _AVAILABLE else "zh-CN"
        self._data = {}
        self.reload()

    def reload(self):
        """加载当前语言, 缺失条目用 zh-CN 兜底。"""
        self._data = {}
        for lng in ("zh-CN", self.lang):  # 先兜底后覆盖: 当前语言优先
            p = os.path.join(self.lang_dir, lng + ".json")
            if os.path.isfile(p):
                try:
                    with open(p, encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        self._data.update(data)
                except Exception:
                    pass

    def tr(self, key, **kw):
        s = self._data.get(key, key)
        if kw:
            try:
                return s.format(**kw)
            except Exception:
                return s
        return s


L = I18n()


def tr(key, **kw):
    return L.tr(key, **kw)
