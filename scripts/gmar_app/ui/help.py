"""
Help page — plain, warm Hebrew explanation of what the app does and how to
use it. Written for someone with no computer background: short sentences,
one idea per card, no jargon. See feedback memory "Non-Technical Progress
UI" (color over numbers, plain language over jargon) — this page follows
the same spirit for the app's static explanation, not just live progress.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget,
)

from . import palette as P
from . import styles as S


def _he_lbl(text: str, size: int = 13, bold: bool = False,
            color: str | None = None) -> QLabel:
    w = QLabel(text)
    w.setWordWrap(True)
    w.setStyleSheet(
        f"color:{color or P.TXT2}; font-family:{P.FONT_STACK}; font-size:{size}px; "
        f"font-weight:{'800' if bold else '500'}; border:none; background:transparent;"
    )
    return w


def _card(radius: int = P.RADIUS_LG, border: str | None = None) -> QWidget:
    w = QWidget()
    w.setStyleSheet(S.card_qss("QWidget", radius=radius, border=border))
    S.apply_card_shadow(w, blur=30, alpha=90, y_offset=10)
    return w


def _screen_card(icon: str, title: str, text: str, color: str) -> QWidget:
    card = _card(radius=P.RADIUS_LG, border=color)
    lay = QHBoxLayout(card)
    lay.setContentsMargins(18, 16, 18, 16)
    lay.setSpacing(16)

    icon_lbl = QLabel(icon)
    icon_lbl.setFixedWidth(50)
    icon_lbl.setAlignment(Qt.AlignCenter)
    icon_lbl.setStyleSheet("font-size:32px; border:none; background:transparent;")
    lay.addWidget(icon_lbl)

    txt_col = QVBoxLayout()
    txt_col.setSpacing(4)
    txt_col.addWidget(_he_lbl(title, 15, bold=True, color=P.TXT))
    txt_col.addWidget(_he_lbl(text, 12, color=P.TXT2))
    lay.addLayout(txt_col, 1)
    return card


class HelpPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLayoutDirection(Qt.RightToLeft)
        self._setup_ui()

    def reset_view(self):
        """Static content — nothing to refresh on re-entry."""
        pass

    def _setup_ui(self):
        self.setStyleSheet(S.window_bg_qss())
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.addWidget(_he_lbl("💡  עזרה — איך זה עובד?", 20, bold=True, color=P.TXT))
        header.addStretch()
        root.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border:none; background:transparent; }")

        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setContentsMargins(2, 4, 2, 16)
        lay.setSpacing(16)

        # ── Intro ────────────────────────────────────────────────────────────
        intro = _card(radius=P.RADIUS_XL, border=P.INDIGO)
        intro_lay = QVBoxLayout(intro)
        intro_lay.setContentsMargins(22, 18, 22, 18)
        intro_lay.setSpacing(8)
        intro_lay.addWidget(_he_lbl("מה זו גמר?", 17, bold=True, color=P.TXT))
        intro_lay.addWidget(_he_lbl(
            "גמר היא תוכנה שאוספת דיווחים על תקיפות במתקני אנרגיה — כמו תחנות "
            "כוח, בתי זיקוק וצינורות — שומרת אותם במקום אחד מסודר, ומנסה לנחש "
            "מה עשוי לקרות בהמשך. קצת כמו תחזית מזג אוויר, רק על תקיפות.",
            13, color=P.TXT2,
        ))
        lay.addWidget(intro)

        # ── Screens ──────────────────────────────────────────────────────────
        lay.addWidget(_he_lbl("איך משתמשים בכל מסך?", 16, bold=True, color=P.TXT))

        screens = [
            ("🏠", "בית",
             "המסך הראשי. מכאן אפשר לקפוץ לכל אחד מהמסכים האחרים בלחיצה אחת.",
             P.INDIGO),
            ("⚡", "תקיפות",
             "כאן בודקים הודעות חדשות מטלגרם אחת־אחת, ומחליטים אם לשמור אותן "
             "כתקיפה חדשה או לעדכן תקיפה שכבר קיימת.",
             P.AMBER),
            ("📡", "שיח",
             "כאן אוספים הודעות מהערוצים כדי לספור כמה פעמים מוזכרים דברים "
             "מסוימים — למשל רחפנים או הגנה אווירית — בכל יום.",
             P.CYAN),
            ("📊", "גרפים",
             "כאן רואים גרף שמראה את כל התקיפות והנתונים לאורך זמן, ואפשר "
             "לבחור מה להציג בו.",
             P.VIOLET),
            ("🤖", "תחזיות",
             "כאן המחשב לומד מהעבר ומנסה לנחש מה עשוי לקרות בקרוב — כמה "
             "מסוכן המצב, איפה, ואיזה סוג תקיפה.",
             P.RED),
            ("✨", "תובנות",
             "כאן מוצגת בשפה פשוטה תמצית של מה שהתחזית 'חושבת' כרגע, בלי "
             "מספרים מסובכים.",
             P.GREEN),
        ]
        for icon, title, text, color in screens:
            lay.addWidget(_screen_card(icon, title, text, color))

        # ── Behind the scenes ────────────────────────────────────────────────
        behind = _card(radius=P.RADIUS_XL, border=P.VIOLET)
        behind_lay = QVBoxLayout(behind)
        behind_lay.setContentsMargins(22, 18, 22, 18)
        behind_lay.setSpacing(8)
        behind_lay.addWidget(_he_lbl("🧠  מה קורה מאחורי הקלעים?", 16, bold=True, color=P.TXT))
        behind_lay.addWidget(_he_lbl(
            "המחשב קורא המון הודעות מטלגרם, וסופר כמה פעמים מוזכרים בהן דברים "
            "מסוימים — למשל רחפנים, פיצוצים או הגנה אווירית. אחר כך הוא משווה "
            "את מה שקורה עכשיו למה שקרה בעבר, בדיוק כמו ילד שפותר תשבץ תמונות: "
            "\"בפעם הקודמת שראיתי הרבה חתיכות כאלה, יצא ציור של כלב — אז כנראה "
            "שגם הפעם זה יהיה כלב\". ככה המחשב מנחש מה עשוי לקרות בהמשך, בלי "
            "להיות בטוח במאה אחוז — בדיוק כמו תחזית מזג אוויר.",
            13, color=P.TXT2,
        ))
        lay.addWidget(behind)

        # ── Closing note ─────────────────────────────────────────────────────
        lay.addWidget(_he_lbl(
            "זהו! עכשיו אתם יודעים איך הכול עובד. אפשר להתחיל לחקור 🎉",
            13, color=P.TXT3,
        ))

        lay.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
