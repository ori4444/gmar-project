"""
Main application window with dark sidebar navigation.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
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

APP_INSTANCE = None


def get_app():
    global APP_INSTANCE
    app = QApplication.instance()
    if app is not None:
        return app
    import sys
    APP_INSTANCE = QApplication(sys.argv)
    APP_INSTANCE.setFont(QFont(P.FONT_FAMILY, P.FONT_SIZE))
    return APP_INSTANCE


# ─────────────────────────────────────────────────────────────────────────────

class SidebarButton(QPushButton):
    def __init__(self, icon: str, label: str, parent=None):
        super().__init__(parent)
        self.setText(f"{icon}\n{label}")
        self.setFixedWidth(90)
        self.setFixedHeight(80)
        self.setCursor(Qt.PointingHandCursor)
        self.setCheckable(True)
        self._set_idle()

    def _set_idle(self):
        self.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {P.NAV_IDLE_TXT};
                border: none; border-radius: 12px;
                font-size: 11px; font-weight: 700;
                padding: 6px 4px;
            }}
            QPushButton:hover {{
                background: {P.CARD_BG}; color: {P.TXT};
            }}
        """)

    def set_active(self, active: bool):
        if active:
            self.setStyleSheet(f"""
                QPushButton {{
                    background: {P.INDIGO}; color: #fff;
                    border: none; border-radius: 12px;
                    font-size: 11px; font-weight: 800;
                    padding: 6px 4px;
                }}
            """)
        else:
            self._set_idle()


class Sidebar(QWidget):
    def __init__(self, sections: list[tuple[str, str, QWidget]], stack: QStackedWidget, parent=None):
        super().__init__(parent)
        self.setFixedWidth(106)
        self.setStyleSheet(f"background:{P.SIDEBAR_BG};")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 16, 8, 16)
        lay.setSpacing(6)
        lay.setAlignment(Qt.AlignTop)

        # App logo/title
        logo = QLabel("G")
        logo.setAlignment(Qt.AlignCenter)
        logo.setFixedHeight(50)
        logo.setStyleSheet(
            f"color:{P.INDIGO}; font-size:28px; font-weight:900; "
            f"background:{P.BG}; border-radius:12px; margin-bottom:12px;"
        )
        lay.addWidget(logo)

        self._btns: list[SidebarButton] = []
        self._pages: list[QWidget] = []
        self._stack = stack

        for i, (icon, label, page) in enumerate(sections):
            btn = SidebarButton(icon, label)
            btn.clicked.connect(lambda _, idx=i: self._switch(idx))
            self._btns.append(btn)
            self._pages.append(page)
            stack.addWidget(page)
            lay.addWidget(btn)

        lay.addStretch()
        self._active = 0
        self._switch(0)

    def _switch(self, idx: int):
        self._active = idx
        self._stack.setCurrentIndex(idx)
        for i, btn in enumerate(self._btns):
            btn.set_active(i == idx)


class GmarMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Gmar — Attack Intelligence")
        self.resize(1280, 820)
        self.setMinimumSize(1100, 700)
        self.setStyleSheet(f"QMainWindow {{ background:{P.BG}; }}")

        self._stack = QStackedWidget()
        self._sidebar = None   # populated in set_pages()

    def set_pages(self, sections: list[tuple[str, str, QWidget]]):
        """
        sections = [(icon, label, page_widget), ...]
        Must be called before show().
        """
        body = QWidget()
        body.setStyleSheet(f"background:{P.BG};")
        h = QHBoxLayout(body)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        self._sidebar = Sidebar(sections, self._stack)
        h.addWidget(self._sidebar)

        sep = QWidget()
        sep.setFixedWidth(1)
        sep.setStyleSheet(f"background:{P.CARD_BORDER};")
        h.addWidget(sep)

        h.addWidget(self._stack)
        self.setCentralWidget(body)

    def switch_to(self, idx: int):
        if self._sidebar:
            self._sidebar._switch(idx)

    def apply_dark_stylesheet(self):
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{
                background: {P.BG}; color: {P.TXT};
                font-family: {P.FONT_FAMILY}; font-size: {P.FONT_SIZE}pt;
            }}
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{
                background: {P.SIDEBAR_BG}; width: 8px; border-radius: 4px;
            }}
            QScrollBar::handle:vertical {{
                background: {P.CARD_BORDER}; border-radius: 4px; min-height: 30px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QPlainTextEdit, QTextEdit {{
                background:{P.INPUT_BG}; color:{P.TXT};
                border:2px solid {P.CARD_BORDER}; border-radius:8px;
                font-size:12px;
            }}
            QCalendarWidget {{ background:{P.CARD_BG}; color:{P.TXT}; }}
            QCalendarWidget QAbstractItemView:enabled {{
                background:{P.CARD_BG}; color:{P.TXT};
                selection-background-color:{P.INDIGO}; selection-color:#fff;
            }}
            QCalendarWidget QWidget#qt_calendar_navigationbar {{
                background:{P.SIDEBAR_BG};
            }}
            QMessageBox {{ background:{P.CARD_BG}; color:{P.TXT}; }}
            QMessageBox QPushButton {{
                background:{P.INDIGO}; color:#fff;
                border:none; border-radius:8px;
                padding:6px 18px; font-weight:700;
            }}
            QToolTip {{
                background:{P.CARD_BG}; color:{P.TXT};
                border:1px solid {P.CARD_BORDER};
            }}
        """)


_MAIN_WINDOW: GmarMainWindow | None = None


def get_main_window() -> GmarMainWindow:
    global _MAIN_WINDOW
    if _MAIN_WINDOW is None:
        _MAIN_WINDOW = GmarMainWindow()
    return _MAIN_WINDOW
