"""
Home page — main navigation hub.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from . import palette as P


class HomePage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._nav_cbs: dict[str, callable] = {}
        self._setup_ui()

    def set_nav(
        self,
        attacks: callable,
        discourse: callable,
        graphs: callable,
        multi_target: callable | None = None,
        predictions: callable | None = None,
    ):
        self._nav_cbs = {
            "attacks":      attacks,
            "discourse":    discourse,
            "graphs":       graphs,
            "multi_target": multi_target or (lambda: None),
            "predictions":  predictions  or (lambda: None),
        }

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(60, 50, 60, 50)
        root.setSpacing(0)
        root.setAlignment(Qt.AlignCenter)

        # Logo
        logo_lbl = QLabel("G")
        logo_lbl.setAlignment(Qt.AlignCenter)
        logo_lbl.setFixedHeight(84)
        logo_lbl.setStyleSheet(
            f"color:{P.INDIGO}; font-size:64px; font-weight:900; border:none;"
        )
        root.addWidget(logo_lbl)

        title_lbl = QLabel("G  M  A  R")
        title_lbl.setAlignment(Qt.AlignCenter)
        title_lbl.setStyleSheet(
            f"color:{P.TXT}; font-size:28px; font-weight:900; "
            f"letter-spacing:10px; border:none; padding-bottom:6px;"
        )
        root.addWidget(title_lbl)

        sub_lbl = QLabel("Energy Infrastructure Attack Analysis System")
        sub_lbl.setAlignment(Qt.AlignCenter)
        sub_lbl.setStyleSheet(
            f"color:{P.TXT2}; font-size:15px; font-weight:500; "
            f"border:none; padding-bottom:48px;"
        )
        root.addWidget(sub_lbl)

        # Cards row
        cards_row = QHBoxLayout()
        cards_row.setSpacing(24)
        cards_row.setAlignment(Qt.AlignCenter)

        card_specs = [
            ("📊", "Graphs / Analysis",
             "Display interactive timeline\nof attacks and discourse",
             P.VIOLET, "graphs"),
            ("⚡", "Attacks",
             "Review and update attack events\non energy infrastructure",
             P.AMBER, "attacks"),
            ("📡", "Discourse",
             "Process daily discourse data\nfrom Telegram",
             P.CYAN, "discourse"),
            ("🎯", "Multi-Target",
             "Discover what attack types\nfollow each discourse pattern",
             P.GREEN, "multi_target"),
            ("🤖", "Predictions",
             "Run ML predictions on\nupcoming attacks",
             P.INDIGO, "predictions"),
        ]

        for icon, title, desc, color, key in card_specs:
            card = self._make_card(icon, title, desc, color, key)
            cards_row.addWidget(card)

        root.addLayout(cards_row)
        root.addStretch()

        info_lbl = QLabel("Select a screen to get started")
        info_lbl.setAlignment(Qt.AlignCenter)
        info_lbl.setStyleSheet(
            f"color:{P.TXT3}; font-size:12px; border:none; "
            f"padding-top:20px;"
        )
        root.addWidget(info_lbl)

    def _make_card(self, icon: str, title: str, desc: str, color: str, key: str) -> QFrame:
        frame = QFrame()
        frame.setFixedSize(270, 290)
        # Cloud: soft cream fill, no border, hover shows subtle accent edge
        frame.setStyleSheet(
            f"QFrame#card_{key} {{ "
            f"background:{P.INPUT_BG}; "
            f"border: 1px solid transparent; "
            f"border-radius:20px; }}"
            f"QFrame#card_{key}:hover {{ border-color:{color}; }}"
        )
        frame.setObjectName(f"card_{key}")

        lay = QVBoxLayout(frame)
        lay.setContentsMargins(24, 26, 24, 20)
        lay.setSpacing(10)
        lay.setAlignment(Qt.AlignCenter)

        icon_lbl = QLabel(icon)
        icon_lbl.setAlignment(Qt.AlignCenter)
        icon_lbl.setStyleSheet(f"font-size:52px; border:none;")
        lay.addWidget(icon_lbl)

        title_lbl = QLabel(title)
        title_lbl.setAlignment(Qt.AlignCenter)
        title_lbl.setStyleSheet(
            f"color:{P.TXT}; font-size:17px; font-weight:900; border:none;"
        )
        lay.addWidget(title_lbl)

        desc_lbl = QLabel(desc)
        desc_lbl.setAlignment(Qt.AlignCenter)
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet(
            f"color:{P.TXT2}; font-size:13px; line-height:1.5; "
            f"border:none;"
        )
        lay.addWidget(desc_lbl)

        lay.addStretch()

        go_btn = QPushButton("Open  →")
        go_btn.setFixedHeight(42)
        go_btn.setCursor(Qt.PointingHandCursor)
        go_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{color};"
            f"border:2px solid {color};border-radius:11px;"
            f"font-size:14px;font-weight:800;}}"
            f"QPushButton:hover{{background:{color};color:#fff;}}"
            f"QPushButton:pressed{{background:{color}cc;color:#fff;}}"
        )
        go_btn.clicked.connect(lambda checked=False, k=key: self._nav_cbs.get(k, lambda: None)())
        lay.addWidget(go_btn)

        return frame
