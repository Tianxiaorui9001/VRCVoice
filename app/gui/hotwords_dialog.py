# -*- coding: utf-8 -*-
"""识别热词编辑器: GUI 管理热词表文件(词 + 权重)。
仅本地识别后端生效(modified_beam_search + hotwords 偏置), 专有名词/人名/游戏名等更准。
保存成功后调用 self.on_saved() 回调(由主窗口决定何时重建识别引擎)。
"""
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout,
                               QHeaderView, QTableWidget, QTableWidgetItem,
                               QVBoxLayout)

from qfluentwidgets import (BodyLabel, DoubleSpinBox, InfoBar, InfoBarPosition,
                            LineEdit, PrimaryPushButton, PushButton,
                            SubtitleLabel)

from ..asr_engine import HOTWORDS_TEMPLATE
from ..i18n import tr
from ..log import data_dir


def _template_header() -> str:
    """从模板里取注释行(保存时保留说明, 词行全部由 GUI 管理)。"""
    return "".join(l for l in HOTWORDS_TEMPLATE.splitlines(True)
                   if l.strip().startswith("#"))


class HotwordsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.on_saved = None
        self._path = os.path.join(data_dir(), "hotwords.txt")
        self.setWindowTitle(tr("识别热词编辑器"))
        self.setMinimumSize(560, 440)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self._build_ui()
        self._load()

    # ---------- UI ----------
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(24, 20, 24, 20)

        title = SubtitleLabel(tr("识别热词"))
        layout.addWidget(title)

        tip = BodyLabel(tr("专有名词(人名/游戏名等)识别不准时, 把词加进来并调权重; "
                           "权重越大识别时越偏向该词(建议 1.0~5.0)。仅本地识别后端生效。"))
        tip.setWordWrap(True)
        tip.setStyleSheet("color: rgba(255,255,255,0.6);")
        layout.addWidget(tip)

        # 表格: 词汇 | 权重
        self.table = QTableWidget(0, 2, self)
        self.table.setHorizontalHeaderLabels([tr("词汇"), tr("权重")])
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(1, 120)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table, 1)

        # 行操作按钮
        row_btns = QHBoxLayout()
        add_btn = PushButton(tr("添加词汇"))
        add_btn.clicked.connect(lambda _=False: self._add_row())
        del_btn = PushButton(tr("删除选中"))
        del_btn.clicked.connect(lambda _=False: self._del_selected())
        row_btns.addWidget(add_btn)
        row_btns.addWidget(del_btn)
        row_btns.addStretch(1)
        layout.addLayout(row_btns)

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

    # ---------- 数据 ----------
    def _load(self):
        rows = []
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    for line in f:
                        s = line.strip()
                        if not s or s.startswith("#"):
                            continue
                        parts = s.split()
                        word = parts[0]
                        score = 1.0
                        if len(parts) > 1:
                            try:
                                score = float(parts[1])
                            except ValueError:
                                score = 1.0
                        rows.append((word, score))
            except OSError:
                rows = []
        if not rows:
            # 空表也给一行空行方便直接输入
            rows = [("", 1.0)]
        for word, score in rows:
            self._add_row(word, score)

    def _add_row(self, word: str = "", score: float = 1.0):
        r = self.table.rowCount()
        self.table.insertRow(r)

        w_edit = LineEdit(self)
        w_edit.setText(word)
        w_edit.setPlaceholderText(tr("输入词汇…"))
        self.table.setCellWidget(r, 0, w_edit)

        s_spin = DoubleSpinBox(self)
        s_spin.setRange(0.5, 10.0)
        s_spin.setSingleStep(0.5)
        s_spin.setDecimals(1)
        s_spin.setValue(score)
        s_spin.setFixedWidth(100)
        self.table.setCellWidget(r, 1, s_spin)

        self.table.setRowHeight(r, 44)

    def _del_selected(self):
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)

    def _collect(self):
        """返回 [(word, score)]: 跳过空词汇行。"""
        out = []
        for r in range(self.table.rowCount()):
            w_item = self.table.cellWidget(r, 0)
            word = (w_item.text() or "").strip() if w_item else ""
            s_item = self.table.cellWidget(r, 1)
            score = float(s_item.value()) if s_item else 1.0
            if word:
                out.append((word, score))
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
