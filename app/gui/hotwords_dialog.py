# -*- coding: utf-8 -*-
"""识别热词编辑器: GUI 管理热词表文件(词 + 权重)。
仅本地识别后端生效(modified_beam_search + hotwords 偏置), 专有名词/人名/游戏名等更准。
保存成功后调用 self.on_saved() 回调(由主窗口决定何时重建识别引擎)。

界面: 两区词条 —— 用户词条(卡片行: 词 + 权重 + 删除) 与 预设引用(一行一条,
JSON 预设文件, 不展开混入; 保存时预设词展开追加在用户词之后, 用注释标记分隔,
重新打开仍恢复为独立的预设行)。
预设文件 JSON 格式: {"name": "名称", "words": [{"word": "...", "score": 3.0}]}
"""
import json
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QScrollArea,
                               QVBoxLayout, QWidget)

import qfluentwidgets
from qfluentwidgets import (BodyLabel, CaptionLabel, DoubleSpinBox, FluentIcon,
                            InfoBar, InfoBarPosition, LineEdit, PrimaryPushButton,
                            PushButton, SubtitleLabel)

from ..asr_engine import HOTWORDS_TEMPLATE
from ..i18n import tr
from ..log import data_dir, log

PRESET_MARK = "# ===PRESET==="  # 热词文件里预设段的注释标记(引擎会跳过注释行)


def _template_header() -> str:
    """从模板里取注释行(保存时保留说明, 词行全部由 GUI 管理)。"""
    return "".join(l for l in HOTWORDS_TEMPLATE.splitlines(True)
                   if l.strip().startswith("#"))


def _presets_dir() -> str:
    """预设词表目录(数据目录/presets), 不存在则创建。保持为空目录, 不预置示例文件。"""
    d = os.path.join(data_dir(), "presets")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError as e:
        log(f"[hotwords] 创建预设目录失败: {e}")
    return d


def parse_words(text: str):
    """解析热词文本 → [(word, score)]: 跳过空行/# 注释(含 BOM 前缀), 忽略坏行。
    行格式: 词 [权重]。若最后一段是数字则视为权重, 其余部分拼成词(词可含空格)。"""
    out = []
    for line in text.splitlines():
        s = line.strip().lstrip("\ufeff")
        if not s or s.startswith("#"):
            continue
        parts = s.split()
        score = 1.0
        if len(parts) > 1:
            try:
                score = float(parts[-1])
                word = " ".join(parts[:-1])
            except ValueError:
                score = 1.0
                word = s
        else:
            word = parts[0]
        if word:
            out.append((word, score))
    return out


def load_preset(path: str):
    """读取预设 JSON → (name, [(word, score)])。失败返回 None。"""
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        log(f"[hotwords] 预设解析失败 {path}: {e}")
        return None
    name = data.get("name") or os.path.splitext(os.path.basename(path))[0]
    words = []
    raw = data.get("words", [])
    if not isinstance(raw, list):
        return None
    for item in raw:
        if isinstance(item, dict):
            w = str(item.get("word", "")).strip()
            try:
                s = float(item.get("score", 1.0))
            except (TypeError, ValueError):
                s = 1.0
        elif isinstance(item, (list, tuple)) and len(item) >= 1:
            w = str(item[0]).strip()
            try:
                s = float(item[1]) if len(item) > 1 else 1.0
            except (TypeError, ValueError):
                s = 1.0
        elif isinstance(item, str):
            w = item.strip()
            s = 1.0
        else:
            continue
        if w:
            words.append((w, s))
    return name, words


def _row_style(dark: bool) -> str:
    bg = "rgba(255,255,255,0.07)" if dark else "rgba(0,0,0,0.05)"
    hover = "rgba(255,255,255,0.12)" if dark else "rgba(0,0,0,0.09)"
    return (f"#wordRow {{ background: {bg}; border-radius: 8px; }}"
            f"#wordRow:hover {{ background: {hover}; }}")


def _secondary_color(dark: bool) -> str:
    return "rgba(255,255,255,0.6)" if dark else "rgba(0,0,0,0.55)"


class _HotwordRow(QWidget):
    """词条卡片行: 词输入 + 权重 + 删除。on_remove(self) 由归属方决定删除行为。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("wordRow")
        self.on_remove = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 10, 6)
        layout.setSpacing(8)

        self.word_edit = LineEdit(self)
        self.word_edit.setPlaceholderText(tr("输入词汇…"))
        layout.addWidget(self.word_edit, 1)

        self.score_spin = DoubleSpinBox(self)
        self.score_spin.setRange(0.5, 10.0)
        self.score_spin.setSingleStep(0.5)
        self.score_spin.setDecimals(1)
        self.score_spin.setValue(1.0)
        self.score_spin.setFixedWidth(128)
        layout.addWidget(self.score_spin)

        self.remove_btn = PushButton(tr("删除"), self)
        self.remove_btn.setFixedWidth(64)
        self.remove_btn.clicked.connect(lambda _=False: self._on_remove())
        layout.addWidget(self.remove_btn)

        self.setStyleSheet(_row_style(qfluentwidgets.isDarkTheme()))

    def _on_remove(self):
        if self.on_remove:
            self.on_remove(self)

    def word(self) -> str:
        return (self.word_edit.text() or "").strip()

    def score(self) -> float:
        return float(self.score_spin.value())


class _PresetRow(QWidget):
    """预设分组行: 默认折叠成一行(名称 + 词数 + 路径 + 展开/移除);
    展开后显示组内词条卡片行, 与用户自定义词条一样可改词/调权重/删除,
    词删光则整组移除。on_empty(self) 由对话框处理整组移除。"""

    def __init__(self, name: str, path: str, words, parent=None):
        super().__init__(parent)
        self.name = name
        self.path = path
        self.on_empty = None
        self._expanded = False
        self._word_rows = []  # [_HotwordRow]

        dark = qfluentwidgets.isDarkTheme()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 头部单行卡片(折叠态就是这一行)
        self.header = QWidget(self)
        self.header.setObjectName("presetHeader")
        hl = QHBoxLayout(self.header)
        hl.setContentsMargins(12, 6, 10, 6)
        hl.setSpacing(8)
        from qfluentwidgets import IconWidget
        ic = IconWidget(FluentIcon.FOLDER, self.header)
        ic.setFixedSize(16, 16)
        hl.addWidget(ic)
        name_lbl = BodyLabel(name, self.header)
        hl.addWidget(name_lbl)
        self.count_lbl = CaptionLabel("", self.header)
        self.count_lbl.setStyleSheet(f"color: {_secondary_color(dark)};")
        hl.addWidget(self.count_lbl)
        path_lbl = CaptionLabel(os.path.basename(path), self.header)
        path_lbl.setStyleSheet(f"color: {_secondary_color(dark)};")
        path_lbl.setMaximumWidth(200)
        hl.addWidget(path_lbl, 1)
        self.toggle_btn = PushButton(tr("展开"), self.header)
        self.toggle_btn.setFixedWidth(64)
        self.toggle_btn.clicked.connect(lambda _=False: self.toggle())
        hl.addWidget(self.toggle_btn)
        self.remove_btn = PushButton(tr("移除"), self.header)
        self.remove_btn.setFixedWidth(64)
        hl.addWidget(self.remove_btn)
        self.header.setStyleSheet(
            f"#presetHeader {{ background: {'rgba(255,255,255,0.07)' if dark else 'rgba(0,0,0,0.05)'};"
            " border-radius: 8px; }"
            f"#presetHeader:hover {{ background: {'rgba(255,255,255,0.12)' if dark else 'rgba(0,0,0,0.09)'}; }}")
        outer.addWidget(self.header)

        # 展开区(默认隐藏): 词条卡片行, 与用户自定义词条同款
        self.expand = QWidget(self)
        self.expand_layout = QVBoxLayout(self.expand)
        self.expand_layout.setContentsMargins(26, 4, 8, 4)
        self.expand_layout.setSpacing(6)
        self.expand.setVisible(False)
        outer.addWidget(self.expand)

        for word, score in words:
            self._add_word(word, score)

    # ---------- 展开 / 折叠 ----------
    def toggle(self):
        self._expanded = not self._expanded
        self.expand.setVisible(self._expanded)
        self.toggle_btn.setText(tr("收起") if self._expanded else tr("展开"))

    # ---------- 词条管理 ----------
    def _add_word(self, word: str, score: float):
        row = _HotwordRow(self.expand)
        row.word_edit.setText(word)
        row.score_spin.setValue(score)
        row.on_remove = lambda r: self._remove_word(r)
        self.expand_layout.addWidget(row)
        self._word_rows.append(row)
        self._update_count()
        return row

    def _remove_word(self, row):
        if row in self._word_rows:
            self._word_rows.remove(row)
            self.expand_layout.removeWidget(row)
            row.deleteLater()
            self._update_count()
            if not self._word_rows and self.on_empty:
                self.on_empty(self)

    def _update_count(self):
        self.count_lbl.setText(tr(f"{len(self._word_rows)} 个词"))

    def words(self):
        """展开区当前词条(实时可编辑结果)。"""
        out = []
        for row in self._word_rows:
            w = row.word()
            if w:
                out.append((w, row.score()))
        return out


class HotwordsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.on_saved = None
        self._path = os.path.join(data_dir(), "hotwords.txt")
        self.setWindowTitle(tr("识别热词"))
        self.setMinimumSize(620, 460)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self._rows = []  # [_HotwordRow | _PresetRow], 用户词在前, 预设在后
        self._build_ui()
        self._load()

    # ---------- UI ----------
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(24, 20, 24, 20)

        title = SubtitleLabel(tr("识别热词"))
        layout.addWidget(title)

        dark = qfluentwidgets.isDarkTheme()
        tip = BodyLabel(tr("专有名词(人名/游戏名等)识别不准时, 把词加进来并调权重; "
                           "权重越大识别时越偏向该词(建议 1.0~5.0)。仅本地识别后端生效。"))
        tip.setWordWrap(True)
        tip.setStyleSheet(f"color: {_secondary_color(dark)};")
        layout.addWidget(tip)

        # 词条列表(滚动区 + 卡片行)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        container = QWidget()
        self.list_layout = QVBoxLayout(container)
        self.list_layout.setContentsMargins(2, 2, 6, 2)
        self.list_layout.setSpacing(6)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(container)
        layout.addWidget(self.scroll, 1)

        # 底部操作区
        ops = QHBoxLayout()
        add_btn = PushButton(FluentIcon.ADD, tr("添加"))
        add_btn.clicked.connect(lambda _=False: self._add_word_row())
        import_btn = PushButton(FluentIcon.FOLDER_ADD, tr("导入预设…"))
        import_btn.clicked.connect(lambda _=False: self._import_file())
        presets_btn = PushButton(FluentIcon.FOLDER, tr("预设目录"))
        presets_btn.clicked.connect(lambda _=False: self._open_presets())
        ops.addWidget(add_btn)
        ops.addWidget(import_btn)
        ops.addWidget(presets_btn)
        ops.addStretch(1)
        layout.addLayout(ops)

        # 保存 / 取消
        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel_btn = PushButton(tr("取消"))
        cancel_btn.clicked.connect(lambda _=False: self.reject())
        save_btn = PrimaryPushButton(tr("保存"))
        save_btn.clicked.connect(lambda _=False: self._save())
        btns.addWidget(cancel_btn)
        btns.addWidget(save_btn)
        layout.addLayout(btns)

    # ---------- 行管理 ----------
    def _insert_row(self, widget, before_preset: bool):
        """插入行: before_preset=True 插到第一个预设行前, 否则插到列表尾(stretch 前)。"""
        if before_preset:
            for i, r in enumerate(self._rows):
                if isinstance(r, _PresetRow):
                    self.list_layout.insertWidget(i, widget)
                    self._rows.insert(i, widget)
                    return
        self.list_layout.insertWidget(self.list_layout.count() - 1, widget)
        self._rows.append(widget)

    def _add_word_row(self, word: str = "", score: float = 1.0):
        row = _HotwordRow(self)
        row.word_edit.setText(word)
        row.score_spin.setValue(score)
        row.on_remove = lambda r: self._remove_row(r)
        self._insert_row(row, before_preset=True)
        return row

    def _add_preset_row(self, name: str, path: str, words):
        row = _PresetRow(name, path, words, self)
        row.on_empty = lambda r: self._remove_row(r)
        row.remove_btn.clicked.connect(lambda _=False, r=row: self._remove_row(r))
        self._insert_row(row, before_preset=False)
        return row

    def _remove_row(self, row):
        if row in self._rows:
            self._rows.remove(row)
            self.list_layout.removeWidget(row)
            row.deleteLater()

    def _load(self):
        """解析 hotwords.txt: 无标记词行 → 用户词; PRESET 标记段 → 预设行。"""
        user_words = []
        presets = []  # (name, path, words)
        cur = None  # 当前预设段
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8-sig") as f:
                    for line in f.read().splitlines():
                        s = line.strip()
                        if s.startswith(PRESET_MARK):
                            if cur:
                                presets.append(cur)
                            rest = s[len(PRESET_MARK):].strip()
                            if "|" in rest:
                                name, path = rest.rsplit("|", 1)
                            else:
                                name, path = rest, ""
                            cur = (name.strip(), path.strip(), [])
                        elif cur is not None:
                            cur[2].extend(parse_words(line))
                        else:
                            user_words.extend(parse_words(line))
                    if cur:
                        presets.append(cur)
            except OSError:
                pass
        # 列表默认全空(用户自行添加/导入)
        for word, score in user_words:
            self._add_word_row(word, score)
        for name, path, words in presets:
            if path and os.path.exists(path):
                self._add_preset_row(name, path, words)
            else:
                # 预设文件已丢失: 词直接并入用户区, 不丢数据
                for word, score in words:
                    self._add_word_row(word, score)

    def _import_file(self):
        """导入预设文件(json 优先, 兼容 txt), 作为独立预设行添加(不混入用户词)。"""
        start = _presets_dir()
        path, _ = QFileDialog.getOpenFileName(
            self, tr("导入预设"), start,
            tr("预设文件 (*.json);;文本词表 (*.txt);;所有文件 (*)"))
        if not path:
            return
        # 同路径去重
        if any(isinstance(r, _PresetRow) and os.path.normcase(r.path) == os.path.normcase(path)
               for r in self._rows):
            InfoBar.warning(tr("已存在"), tr("该预设已经在列表里了"), parent=self,
                            position=InfoBarPosition.TOP, duration=3000)
            return
        ext = os.path.splitext(path)[1].lower()
        if ext == ".json":
            loaded = load_preset(path)
            if loaded is None:
                InfoBar.error(tr("导入失败"),
                              tr('JSON 格式参考: {"name": "词表名", "words": [{"word": "词", "score": 2.0}]}'),
                              parent=self, position=InfoBarPosition.TOP, duration=5000)
                return
            name, words = loaded
        else:
            try:
                with open(path, "r", encoding="utf-8-sig") as f:
                    words = parse_words(f.read())
            except OSError as e:
                InfoBar.error(tr("导入失败"), str(e), parent=self,
                              position=InfoBarPosition.TOP, duration=5000)
                return
            name = os.path.splitext(os.path.basename(path))[0]
        if not words:
            InfoBar.warning(tr("没有可导入的词条"), tr("文件里没有找到「词 权重」"), parent=self,
                            position=InfoBarPosition.TOP, duration=4000)
            return
        self._add_preset_row(name, path, words)
        InfoBar.success(tr("已导入预设"), tr(f"「{name}」共 {len(words)} 个词"), parent=self,
                        position=InfoBarPosition.TOP, duration=3000)

    def _open_presets(self):
        d = _presets_dir()
        try:
            os.startfile(d)  # noqa: 打开资源管理器
        except OSError as e:
            InfoBar.error(tr("打开失败"), str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)

    # ---------- 保存 ----------
    def _collect_user_words(self):
        out = []
        for row in self._rows:
            if isinstance(row, _HotwordRow):
                w = row.word()
                if w:
                    out.append((w, row.score()))
        return out

    def _collect_presets(self):
        return [r for r in self._rows if isinstance(r, _PresetRow)]

    def _save(self):
        user = self._collect_user_words()
        presets = self._collect_presets()
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                f.write(_template_header())
                for word, score in user:
                    f.write(f"{word} {score:g}\n")
                for row in presets:
                    f.write(f"{PRESET_MARK} {row.name}|{row.path}\n")
                    for word, score in row.words():
                        f.write(f"{word} {score:g}\n")
        except OSError as e:
            InfoBar.error(tr("保存失败"), str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=5000)
            return
        if self.on_saved:
            try:
                self.on_saved()
            except Exception as e:
                log(f"[hotwords] on_saved 回调异常: {e}")
        InfoBar.success(tr("已保存"), tr("识别热词已更新"), parent=self,
                        position=InfoBarPosition.TOP, duration=3000)
        self.accept()
