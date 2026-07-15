"""
Home page — main navigation hub.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

from . import palette as P
from . import styles as S


class _NavCard(QFrame):
    """
    The entire card is the button — floating, shadow-lifted, and rising
    further on hover for a tangible 3D/airborne feel.
    """

    clicked = Signal()

    def __init__(self, accent: str, parent=None):
        super().__init__(parent)
        self._pressed = False
        self.setCursor(Qt.PointingHandCursor)

        self._idle_qss = S.card_qss("QFrame", radius=P.RADIUS_XL)
        self._hover_qss = (
            f"QFrame {{ background:qlineargradient(x1:0,y1:0,x2:0,y2:1, "
            f"stop:0 {P.CARD_BG_HOVER}, stop:1 {P.CARD_BG_TOP}); "
            f"border:1.5px solid {accent}; border-radius:{P.RADIUS_XL}px; }}"
        )
        self.setStyleSheet(self._idle_qss)

        self._shadow = S.apply_card_shadow(self, blur=44, alpha=110, y_offset=16)
        self._lift = S.HoverLift(
            self._shadow,
            idle=(44, 110, 16),
            hover=(70, 170, 30),
        )

    def enterEvent(self, event):
        self.setStyleSheet(self._hover_qss)
        self._lift.lift()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setStyleSheet(self._idle_qss)
        self._lift.rest()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._pressed = True
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._pressed and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        self._pressed = False
        super().mouseReleaseEvent(event)


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
        predictions: callable | None = None,
    ):
        self._nav_cbs = {
            "attacks":      attacks,
            "discourse":    discourse,
            "graphs":       graphs,
            "predictions":  predictions  or (lambda: None),
        }

    def reset_view(self):
        """No transient state to clear — kept for the sidebar's reset hook."""
        pass

    def _setup_ui(self):
        self.setStyleSheet(S.window_bg_qss())
        root = QVBoxLayout(self)
        root.setContentsMargins(60, 48, 60, 40)
        root.setSpacing(0)
        root.setAlignment(Qt.AlignCenter)

        # Logo
        logo_lbl = QLabel("G")
        logo_lbl.setAlignment(Qt.AlignCenter)
        logo_lbl.setFixedHeight(80)
        logo_lbl.setStyleSheet(
            f"color:{P.INDIGO}; font-family:{P.FONT_STACK}; font-size:58px; "
            f"font-weight:800; border:none;"
        )
        root.addWidget(logo_lbl)

        title_lbl = QLabel("G  M  A  R")
        title_lbl.setAlignment(Qt.AlignCenter)
        title_lbl.setStyleSheet(
            f"color:{P.TXT}; font-family:{P.FONT_STACK}; font-size:23px; font-weight:700; "
            f"letter-spacing:10px; border:none; padding-bottom:8px;"
        )
        root.addWidget(title_lbl)

        sub_lbl = QLabel("מערכת ניתוח תקיפות על תשתיות אנרגיה")
        sub_lbl.setAlignment(Qt.AlignCenter)
        sub_lbl.setStyleSheet(
            f"color:{P.TXT2}; font-family:{P.FONT_STACK}; font-size:14px; font-weight:500; "
            f"border:none; padding-bottom:64px;"
        )
        root.addWidget(sub_lbl)

        # Cards row
        cards_row = QHBoxLayout()
        cards_row.setSpacing(28)
        cards_row.setAlignment(Qt.AlignCenter)

        card_specs = [
            ("📊", "גרפים וניתוח", P.VIOLET, "graphs"),
            ("⚡", "תקיפות",       P.AMBER,  "attacks"),
            ("📡", "שיח",         P.CYAN,   "discourse"),
            ("🤖", "תחזיות",      P.INDIGO, "predictions"),
        ]

        for icon, title, color, key in card_specs:
            card = self._make_card(icon, title, color, key)
            cards_row.addWidget(card)

        root.addLayout(cards_row)
        root.addStretch()

        info_lbl = QLabel("בחרו מסך כדי להתחיל")
        info_lbl.setAlignment(Qt.AlignCenter)
        info_lbl.setStyleSheet(
            f"color:{P.TXT3}; font-family:{P.FONT_STACK}; font-size:12px; border:none; "
            f"padding-top:20px;"
        )
        root.addWidget(info_lbl)

    def _make_card(self, icon: str, title: str, color: str, key: str) -> QFrame:
        card = _NavCard(color)
        card.setFixedSize(230, 230)
        card.setObjectName(f"card_{key}")

        lay = QVBoxLayout(card)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(14)
        lay.setAlignment(Qt.AlignCenter)

        icon_lbl = QLabel(icon)
        icon_lbl.setAlignment(Qt.AlignCenter)
        icon_lbl.setStyleSheet("font-size:52px; border:none; background:transparent;")
        lay.addWidget(icon_lbl)

        title_lbl = QLabel(title)
        title_lbl.setAlignment(Qt.AlignCenter)
        title_lbl.setWordWrap(True)
        title_lbl.setStyleSheet(
            f"color:{P.TXT}; font-family:{P.FONT_STACK}; font-size:16px; font-weight:700; "
            f"border:none; background:transparent;"
        )
        lay.addWidget(title_lbl)

        card.clicked.connect(lambda k=key: self._nav_cbs.get(k, lambda: None)())
        return card
