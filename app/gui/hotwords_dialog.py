# -*- coding: utf-8 -*-
"""识别热词编辑器: GUI 管理热词表文件(词 + 权重)。
仅本地识别后端生效(modified_beam_search + hotwords 偏置), 专有名词/人名/游戏名等更准。
保存成功后调用 self.on_saved() 回调(由主窗口决定何时重建识别引擎)。

界面: 卡片式词条列表(词输入 + 权重 + 删除), 支持从 .txt 导入(格式同热词文件),
以及打开预设目录(数据目录/presets/, 可放收集好的词表文件, 首次自动生成示例)。
"""
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QScrollArea,
                               QVBoxLayout, QWidget)

import qfluentwidgets
from qfluentwidgets import (BodyLabel, DoubleSpinBox, FluentIcon, InfoBar,
                            InfoBarPosition, LineEdit, PrimaryPushButton,
                            PushButton, SubtitleLabel)

from ..asr_engine import HOTWORDS_TEMPLATE
from ..i18n import tr
from ..log import data_dir, log


def _template_header() -> str:
    """从模板里取注释行(保存时保留说明, 词行全部由 GUI 管理)。"""
    return "".join(l for l in HOTWORDS_TEMPLATE.splitlines(True)
                   if l.strip().startswith("#"))


def _presets_dir() -> str:
    """预设词表目录(数据目录/presets), 不存在则创建。"""
    d = os.path.join(data_dir(), "presets")
    try:
        os.makedirs(d, exist_ok=True)
        sample = os.path.join(d, "示例词表.txt")
        if not os.path.exists(sample):
            with open(sample, "w", encoding="utf-8") as f:
                f.write("# 预设词表示例: 每行一个词, 可带权重(建议 1.0~5.0)。\n"
                        "# 在热词编辑器点「导入…」选择本目录文件, 即可追加到热词表。\n"
                        "# 收集到的词表文件都可以丢进这个目录。\n\n"
                        "VRChat 3.0\n"
                        "龙门石窟 2.0\n")
    except OSError as e:
        log(f"[hotwords] 创建预设目录失败: {e}")
    return d


def parse_words(text: str):
    """解析热词文本 → [(word, score)]: 跳过空行/# 注释, 忽略坏行。"""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split()
        score = 1.0
        if len(parts) > 1:
            try:
                score = float(parts[1])
            except ValueError:
                score = 1.0
        if parts[0]:
            out.append((parts[0], score))
    return out


class _HotwordRow(QWidget):
    """一行词条卡片: 词输入 + 权重 + 删除。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("hotwordRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 8, 12, 8)
        layout.setSpacing(10)

        self.word_edit = LineEdit(self)
        self.word_edit.setPlaceholderText(tr("输入词汇…"))
        layout.addWidget(self.word_edit, 1)

        self.score_spin = DoubleSpinBox(self)
        self.score_spin.setRange(0.5, 10.0)
        self.score_spin.setSingleStep(0.5)
        self.score_spin.setDecimals(1)
        self.score_spin.setValue(1.0)
        self.score_spin.setFixedWidth(110)
        layout.addWidget(self.score_spin)

        self.remove_btn = PushButton(tr("删除"), self)
        self.remove_btn.setFixedWidth(72)
        layout.addWidget(self.remove_btn)

        dark = qfluentwidgets.isDarkTheme()
        self.setStyleSheet(
            f"#hotwordRow {{ background: {'rgba(255,255,255,0.07)' if dark else 'rgba(0,0,0,0.05)'};"
            " border-radius: 8px; }"
            "#hotwordRow:hover { background: "
            f"{'rgba(255,255,255,0.12)' if dark else 'rgba(0,0,0,0.09)'}; }}")

    def word(self) -> str:
        return (self.word_edit.text() or "").strip()

    def score(self) -> float:
        return float(self.score_spin.value())


class HotwordsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.on_saved = None
        self._path = os.path.join(data_dir(), "hotwords.txt")
        self.setWindowTitle(tr("识别热词"))
        self.setMinimumSize(620, 480)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self._rows = []  # [_HotwordRow]
        self._build_ui()
        self._load()

    # ---------- UI ----------
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(28, 24, 28, 24)

        title = SubtitleLabel(tr("识别热词"))
        layout.addWidget(title)

        tip = BodyLabel(tr("专有名词(人名/游戏名等)识别不准时, 把词加进来并调权重; "
                           "权重越大识别时越偏向该词(建议 1.0~5.0)。仅本地识别后端生效。"))
        tip.setWordWrap(True)
        tip.setStyleSheet("color: rgba(255,255,255,0.6);")
        layout.addWidget(tip)

        # 词条列表(滚动区 + 卡片行)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        container = QWidget()
        self.list_layout = QVBoxLayout(container)
        self.list_layout.setContentsMargins(2, 2, 6, 2)
        self.list_layout.setSpacing(10)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(container)
        layout.addWidget(self.scroll, 1)

        # 底部操作区: 添加 | 导入 | 预设目录 | 删除
        ops = QHBoxLayout()
        add_btn = PushButton(FluentIcon.ADD, tr("添加"))
        add_btn.clicked.connect(lambda _=False: self._add_row())
        import_btn = PushButton(FluentIcon.FOLDER_ADD, tr("导入…"))
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
    def _add_row(self, word: str = "", score: float = 1.0):
        row = _HotwordRow(self)
        row.word_edit.setText(word)
        row.score_spin.setValue(score)
        row.remove_btn.clicked.connect(lambda _=False, r=row: self._remove_row(r))
        # 插到 stretch 前面
        self.list_layout.insertWidget(self.list_layout.count() - 1, row)
        self._rows.append(row)

    def _remove_row(self, row):
        if row in self._rows:
            self._rows.remove(row)
            self.list_layout.removeWidget(row)
            row.deleteLater()

    def _load(self):
        rows = []
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    rows = parse_words(f.read())
            except OSError:
                rows = []
        if not rows:
            rows = [("", 1.0)]
        for word, score in rows:
            self._add_row(word, score)

    def _import_file(self):
        """从 txt 导入词条(追加, 同词更新权重)。"""
        start = _presets_dir()
        path, _ = QFileDialog.getOpenFileName(
            self, tr("导入热词"), start, tr("文本文件 (*.txt);;所有文件 (*)"))
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                items = parse_words(f.read())
        except OSError as e:
            InfoBar.error(tr("导入失败"), str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=5000)
            return
        if not items:
            InfoBar.warning(tr("没有可导入的词条"), tr("文件里没有找到「词 权重」行"), parent=self,
                            position=InfoBarPosition.TOP, duration=4000)
            return
        # 同词更新权重, 新词追加
        idx = {r.word(): r for r in self._rows if r.word()}
        added = updated = 0
        for word, score in items:
            if word in idx:
                idx[word].score_spin.setValue(score)
                updated += 1
            else:
                self._add_row(word, score)
                idx[word] = self._rows[-1]
                added += 1
        InfoBar.success(tr("导入完成"),
                        tr(f"新增 {added} 条, 更新 {updated} 条"), parent=self,
                        position=InfoBarPosition.TOP, duration=3000)

    def _open_presets(self):
        d = _presets_dir()
        try:
            os.startfile(d)  # noqa: 打开资源管理器
        except OSError as e:
            InfoBar.error(tr("打开失败"), str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=4000)

    # ---------- 保存 ----------
    def _collect(self):
        """返回 [(word, score)]: 跳过空词汇行。"""
        out = []
        for row in self._rows:
            w = row.word()
            if w:
                out.append((w, row.score()))
        return out

    def _save(self):
        rows = self._collect()
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                f.write(_template_header())
                for word, score in rows:
                    f.write(f"{word} {score:g}\n")
        except OSError as e:
            InfoBar.error(tr("保存失败"), str(e), parent=self,
                          position=InfoBarPosition.TOP, duration=5000)
            return
        if self.on_saved:
            try:
                self.on_saved()
            except Exception:
                pass
        InfoBar.success(tr("已保存"), tr("识别热词已更新"), parent=self,
                        position=InfoBarPosition.TOP, duration=3000)
        self.accept()
