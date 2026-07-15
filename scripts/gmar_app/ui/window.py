"""
Main application window — dark, floating-sidebar navigation.
"""
from __future__ import annotations

import glob
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import palette as P
from . import styles as S

APP_INSTANCE = None
_FONTS_LOADED = False


def _load_fonts():
    global _FONTS_LOADED
    if _FONTS_LOADED:
        return
    fonts_dir = os.path.join(os.path.dirname(__file__), "..", "assets", "fonts")
    for path in glob.glob(os.path.join(fonts_dir, "*.ttf")):
        QFontDatabase.addApplicationFont(path)
    _FONTS_LOADED = True


def get_app():
    global APP_INSTANCE
    app = QApplication.instance()
    if app is not None:
        return app
    import sys
    APP_INSTANCE = QApplication(sys.argv)
    _load_fonts()
    APP_INSTANCE.setFont(QFont(P.FONT_FAMILY, P.FONT_SIZE))
    return APP_INSTANCE


# ─────────────────────────────────────────────────────────────────────────────

class SidebarButton(QPushButton):
    """A nav item that floats on its own — full border on every side, its own
    soft shadow, and a lift/press/flash animation — rather than sitting flush
    inside a shared sidebar bar."""

    def __init__(self, icon: str, label: str, accent: str | None = None, parent=None):
        super().__init__(parent)
        self.setText(f"{icon}\n{label}")
        self.setFixedWidth(92)
        self.setFixedHeight(72)
        self.setCursor(Qt.PointingHandCursor)
        self.setCheckable(True)
        self._active = False
        self._accent = accent or P.INDIGO
        self._hover = S.HoverFade(self, color="#ffffff", max_opacity=0.05, radius=P.RADIUS_LG)
        S.bind_floating_button(
            self, idle=(10, 60, 2), hover=(20, 110, 6), pressed=(4, 30, 1),
            flash_color=self._accent, flash_opacity=0.12,
        )
        self._set_idle()

    def enterEvent(self, event):
        if not self._active:
            self._hover.fade_in()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover.fade_out()
        super().leaveEvent(event)

    def _base_qss(self, bg: str, txt: str, border: str, weight: int) -> str:
        return f"""
            QPushButton {{
                background: {bg}; color: {txt};
                border: 1px solid {border};
                border-radius: {P.RADIUS_LG}px;
                font-family: {P.FONT_STACK}; font-size: 10px; font-weight: {weight};
                padding: 6px 4px;
            }}
        """

    def _set_idle(self):
        self._active = False
        self.setStyleSheet(self._base_qss("transparent", P.NAV_IDLE_TXT, P.DIVIDER, 600))

    def set_active(self, active: bool):
        self._active = active
        if active:
            self._hover.fade_out()
            bg = P.with_alpha(self._accent, 0.11)
            self.setStyleSheet(self._base_qss(bg, P.NAV_ACTIVE_TXT, self._accent, 700))
        else:
            self._set_idle()


class Sidebar(QWidget):
    def __init__(self, sections: list[tuple[str, str, QWidget]], stack: QStackedWidget, parent=None):
        super().__init__(parent)
        self.setFixedWidth(112)
        self.setObjectName("sidebar")
        self.setAttribute(Qt.WA_TranslucentBackground)
        # Deliberately no panel background/border/shadow here — nav buttons
        # each float independently (own border + shadow) instead of sitting
        # inside a shared holding bar.

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 20, 10, 16)
        lay.setSpacing(12)
        lay.setAlignment(Qt.AlignTop)

        # App logo/title
        logo = QLabel("G")
        logo.setAlignment(Qt.AlignCenter)
        logo.setFixedHeight(44)
        logo.setStyleSheet(
            f"color:{P.INDIGO}; font-family:{P.FONT_STACK}; font-size:26px; font-weight:800; "
            f"background:transparent; border:none; margin-bottom:14px;"
        )
        lay.addWidget(logo)

        self._btns: list[SidebarButton] = []
        self._pages: list[QWidget] = []
        self._stack = stack

        for i, (icon, label, page) in enumerate(sections):
            accent = P.NAV_ACCENTS[i % len(P.NAV_ACCENTS)]
            btn = SidebarButton(icon, label, accent=accent)
            btn.clicked.connect(lambda _, idx=i: self._switch(idx))
            self._btns.append(btn)
            self._pages.append(page)
            stack.addWidget(page)
            lay.addWidget(btn)

        lay.addStretch()
        self._active = 0
        self._switch(0, animate=False)

    def _switch(self, idx: int, animate: bool = True):
        self._active = idx
        self._stack.setCurrentIndex(idx)
        for i, btn in enumerate(self._btns):
            btn.set_active(i == idx)

        page = self._pages[idx]
        reset = getattr(page, "reset_view", None)
        if callable(reset):
            reset()

        if animate:
            S.fade_in_widget(self._stack.currentWidget())


class GmarMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("גמר — מערכת מעקב תקיפות")
        self.resize(1440, 860)
        self.setMinimumSize(1120, 720)
        self.setStyleSheet(f"QMainWindow {{ {S.window_bg_qss()} }}")

        self._stack = QStackedWidget()
        self._sidebar = None   # populated in set_pages()

    def set_pages(self, sections: list[tuple[str, str, QWidget]]):
        """
        sections = [(icon, label, page_widget), ...]
        Must be called before show().
        """
        body = QWidget()
        body.setStyleSheet(S.window_bg_qss())
        h = QHBoxLayout(body)
        h.setContentsMargins(16, 16, 16, 16)
        h.setSpacing(16)

        self._sidebar = Sidebar(sections, self._stack)
        h.addWidget(self._sidebar)
        h.addWidget(self._stack)
        self.setCentralWidget(body)

    def switch_to(self, idx: int):
        if self._sidebar:
            self._sidebar._switch(idx)

    def apply_dark_stylesheet(self):
        self.setStyleSheet(f"""
            QWidget {{
                background: {P.BG}; color: {P.TXT};
                font-family: {P.FONT_STACK}; font-size: {P.FONT_SIZE}pt;
            }}
            QMainWindow {{ {S.window_bg_qss()} }}
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{
                background: transparent; width: 10px; margin: 2px 0;
            }}
            QScrollBar::handle:vertical {{
                background: {P.BORDER_STRONG}; border-radius: 4px; min-height: 30px;
            }}
            QScrollBar::handle:vertical:hover {{ background: {P.TXT3}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QScrollBar:horizontal {{
                background: transparent; height: 10px; margin: 0 2px;
            }}
            QScrollBar::handle:horizontal {{
                background: {P.BORDER_STRONG}; border-radius: 4px; min-width: 30px;
            }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
            QPlainTextEdit, QTextEdit {{
                background:{P.INPUT_BG}; color:{P.TXT};
                border: 1px solid {P.DIVIDER}; border-radius:{P.RADIUS_MD}px;
                font-family:{P.FONT_MONO}; font-size:12px;
            }}
            QCalendarWidget {{ background:{P.CARD_BG}; color:{P.TXT}; }}
            QCalendarWidget QAbstractItemView:enabled {{
                background:{P.CARD_BG}; color:{P.TXT};
                selection-background-color:{P.INDIGO}; selection-color:#fff;
                font-size:13px;
            }}
            QCalendarWidget QAbstractItemView:disabled {{
                color:{P.TXT3};
            }}
            QCalendarWidget QWidget#qt_calendar_navigationbar {{
                background:{P.INPUT_BG}; min-height:44px;
            }}
            QCalendarWidget QToolButton {{
                color:{P.TXT}; font-size:13px; font-weight:700;
                background:{P.INPUT_BG}; border:none; border-radius:{P.RADIUS_SM}px;
                padding:4px 8px; min-width:32px; min-height:32px;
            }}
            QCalendarWidget QToolButton:hover {{
                background:{P.BORDER_STRONG}; color:#fff;
            }}
            QCalendarWidget QMenu {{
                background:{P.CARD_BG}; color:{P.TXT};
                border: 1px solid {P.DIVIDER}; border-radius:{P.RADIUS_MD}px;
            }}
            QCalendarWidget QSpinBox {{
                background:{P.INPUT_BG}; color:{P.TXT};
                border: 1px solid {P.DIVIDER}; border-radius:{P.RADIUS_SM}px;
                padding:2px 6px; font-size:13px;
            }}
            QCalendarWidget QHeaderView::section {{
                background:{P.INPUT_BG}; color:{P.TXT2};
                font-size:12px; font-weight:700; padding:4px;
                border:none;
            }}
            QMessageBox {{ background:{P.CARD_BG}; color:{P.TXT}; }}
            QMessageBox QPushButton {{
                background:{P.INDIGO}; color:#fff;
                border:none; border-radius:{P.RADIUS_MD}px;
                padding:6px 18px; font-weight:700;
            }}
            QMessageBox QPushButton:hover {{ background:{P.INDIGO_L}; }}
            QToolTip {{
                background:{P.GLASS_BG}; color:{P.TXT};
                border: 1px solid {P.BORDER_STRONG}; border-radius:{P.RADIUS_SM}px;
                padding:4px 8px;
            }}
            QMenu {{
                background:{P.CARD_BG}; color:{P.TXT};
                border:1px solid {P.DIVIDER}; border-radius:{P.RADIUS_MD}px; padding:4px;
            }}
            QMenu::item {{ padding:6px 16px; border-radius:{P.RADIUS_SM}px; }}
            QMenu::item:selected {{ background:{P.INDIGO_BG}; color:{P.TXT}; }}
            QComboBox QAbstractItemView {{
                background:{P.CARD_BG}; color:{P.TXT};
                selection-background-color:{P.INDIGO}; selection-color:#fff;
                border:1px solid {P.DIVIDER};
            }}
        """)


_MAIN_WINDOW: GmarMainWindow | None = None


def get_main_window() -> GmarMainWindow:
    global _MAIN_WINDOW
    if _MAIN_WINDOW is None:
        _MAIN_WINDOW = GmarMainWindow()
    return _MAIN_WINDOW
