"""
Shared style helpers for the dark "intelligence platform" theme.

Centralizes the patterns that would otherwise be hand-duplicated everywhere:
floating-card QSS, soft drop shadows, animated hover fades (QSS has no
`transition`), an indeterminate loading spinner, and a page-switch fade.
"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPointF, QPropertyAnimation, QRectF, QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QGraphicsOpacityEffect, QWidget

from . import palette as P


# ─────────────────────────────────────────────────────────────────────────────
#  Floating-card QSS fragments
# ─────────────────────────────────────────────────────────────────────────────

def card_qss(selector: str = "QFrame", radius: int = P.RADIUS_LG, glass: bool = False,
             border: str | None = None, elevated: bool = True) -> str:
    """QSS fragment for a floating-card surface: subtle top-lit gradient (raised
    look) + hairline border. Pair with apply_card_shadow() for real depth."""
    border_color = border or P.CARD_BORDER
    if glass:
        bg = f"background:{P.GLASS_BG};"
    elif elevated:
        bg = (
            f"background:qlineargradient(x1:0,y1:0,x2:0,y2:1, "
            f"stop:0 {P.CARD_BG_TOP}, stop:1 {P.CARD_BG});"
        )
    else:
        bg = f"background:{P.CARD_BG};"
    return (
        f"{selector} {{ {bg} border:1px solid {border_color}; "
        f"border-radius:{radius}px; }}"
    )


def divider_qss() -> str:
    return f"background:{P.DIVIDER}; border:none;"


def window_bg_qss() -> str:
    """
    Subtle radial gradient for top-level page/window surfaces — the backdrop
    is lit near the center and recedes to near-black at the edges, so floating
    cards read as hovering in space rather than pinned to flat solid black.
    Only ever apply this to ROOT-level containers (window, page outer widget) —
    applying it to small nested widgets makes the gradient tile/repeat oddly.
    """
    return (
        f"background: qradialgradient(cx:0.5, cy:0.22, radius:1.15, fx:0.5, fy:0.22, "
        f"stop:0 {P.BG_GRADIENT_TOP}, stop:0.55 {P.BG}, stop:1 {P.BG_GRADIENT_EDGE});"
    )


def window_bg_css() -> str:
    """Same backdrop gradient in CSS syntax, for embedded HTML/Plotly surfaces."""
    return (
        f"background: radial-gradient(circle at 50% 22%, "
        f"{P.BG_GRADIENT_TOP} 0%, {P.BG} 55%, {P.BG_GRADIENT_EDGE} 100%);"
    )


def apply_card_shadow(widget: QWidget, blur: int = 56, alpha: int = 150,
                       y_offset: int = 20) -> QGraphicsDropShadowEffect:
    """Soft drop shadow with real lift — the native equivalent of CSS box-shadow.
    Defaults are deliberately pronounced so floating cards read as airborne,
    not just flat panels with a hairline border."""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, y_offset)
    effect.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(effect)
    return effect


def apply_button_shadow(widget: QWidget, blur: int = 22, alpha: int = 100,
                         y_offset: int = 7) -> QGraphicsDropShadowEffect:
    """Lighter-weight lift for buttons/pills — enough to read as raised off the
    surface without the heavier treatment reserved for full cards/panels."""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, y_offset)
    effect.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(effect)
    return effect


def set_shadow_depth(effect: QGraphicsDropShadowEffect, blur: int, alpha: int, y_offset: int):
    """Update an existing shadow's params — used to animate lift on hover."""
    effect.setBlurRadius(blur)
    effect.setOffset(0, y_offset)
    effect.setColor(QColor(0, 0, 0, alpha))


# ─────────────────────────────────────────────────────────────────────────────
#  HoverFade / ClickFlash — smooth tint overlays, since QSS can't animate
# ─────────────────────────────────────────────────────────────────────────────

class _OverlayResizeSync(QObject):
    """Keeps a full-cover overlay child sized to its host as the host resizes."""

    def __init__(self, overlay: QWidget, host: QWidget):
        super().__init__(host)
        self._overlay = overlay
        host.installEventFilter(self)
        self.sync()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Resize:
            self.sync()
        return False

    def sync(self):
        host = self._overlay.parentWidget()
        self._overlay.setGeometry(0, 0, host.width(), host.height())
        self._overlay.lower()


def _make_tint_overlay(host: QWidget, color: str, radius: int) -> tuple[QWidget, QGraphicsOpacityEffect]:
    overlay = QWidget(host)
    overlay.setAttribute(Qt.WA_TransparentForMouseEvents)
    overlay.setStyleSheet(f"background:{color}; border-radius:{radius}px;")
    effect = QGraphicsOpacityEffect(overlay)
    effect.setOpacity(0.0)
    overlay.setGraphicsEffect(effect)
    overlay.show()
    _OverlayResizeSync(overlay, host)
    return overlay, effect


class HoverFade:
    """
    A translucent tint layer over a widget that fades in/out on hover (low
    opacity, so it reads as a highlight rather than obscuring content). Call
    `.fade_in()` / `.fade_out()` from the host's enterEvent / leaveEvent.
    """

    def __init__(self, host: QWidget, color: str = "#ffffff", max_opacity: float = 0.06,
                 duration: int = 140, radius: int = 0):
        self._max_opacity = max_opacity
        self._overlay, self._effect = _make_tint_overlay(host, color, radius)
        self._anim = QPropertyAnimation(self._effect, b"opacity")
        self._anim.setDuration(duration)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    def fade_in(self):
        self._animate(self._max_opacity)

    def fade_out(self):
        self._animate(0.0)

    def _animate(self, target: float):
        self._anim.stop()
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(target)
        self._anim.start()


class ClickFlash:
    """
    A brighter, faster tint flash on press/release — the "ink" of a click.
    Wires itself to the host's built-in `pressed`/`released` signals, so it
    works on any QAbstractButton with no subclassing needed. Uses its own
    overlay (like HoverFade) so it can run alongside a QGraphicsDropShadowEffect
    on the same host without the two effects fighting over setGraphicsEffect().
    """

    def __init__(self, host: QWidget, color: str = "#ffffff", max_opacity: float = 0.16,
                 duration: int = 90, radius: int = 0):
        self._overlay, self._effect = _make_tint_overlay(host, color, radius)
        self._anim = QPropertyAnimation(self._effect, b"opacity")
        self._anim.setDuration(duration)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        if hasattr(host, "pressed"):
            host.pressed.connect(self.flash_in)
            host.released.connect(self.flash_out)

    def flash_in(self):
        self._animate(self._max_opacity)

    def flash_out(self):
        self._animate(0.0)

    def _animate(self, target: float):
        self._anim.stop()
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(target)
        self._anim.start()


class HoverLift:
    """
    Animates a drop-shadow so a widget feels like it lifts off the surface on
    hover and presses down into it on click — bigger/softer blur and offset
    for hover, tighter/darker for a pressed dip. Pairs with a
    QGraphicsDropShadowEffect from apply_card_shadow() / apply_button_shadow().
    """

    def __init__(self, effect: QGraphicsDropShadowEffect,
                 idle: tuple[int, int, int] = (56, 150, 20),
                 hover: tuple[int, int, int] = (84, 210, 34),
                 pressed: tuple[int, int, int] = (24, 90, 6),
                 duration: int = 160):
        self._effect = effect
        self._idle = idle
        self._hover = hover
        self._pressed = pressed
        self._default_duration = duration
        self._blur_anim = QPropertyAnimation(effect, b"blurRadius")
        self._blur_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._offset_anim = QPropertyAnimation(effect, b"offset")
        self._offset_anim.setEasingCurve(QEasingCurve.OutCubic)

    def lift(self):
        self._animate(self._hover, self._default_duration)

    def rest(self):
        self._animate(self._idle, self._default_duration)

    def press(self):
        self._animate(self._pressed, 90)

    def release(self, hovered: bool = False):
        self._animate(self._hover if hovered else self._idle, 120)

    def _animate(self, target: tuple[int, int, int], duration: int):
        blur, alpha, y = target
        self._effect.setColor(QColor(0, 0, 0, alpha))
        self._blur_anim.stop()
        self._blur_anim.setDuration(duration)
        self._blur_anim.setStartValue(self._effect.blurRadius())
        self._blur_anim.setEndValue(blur)
        self._blur_anim.start()
        self._offset_anim.stop()
        self._offset_anim.setDuration(duration)
        self._offset_anim.setStartValue(self._effect.offset())
        self._offset_anim.setEndValue(QPointF(0, y))
        self._offset_anim.start()


def bind_floating_button(widget: QWidget, idle: tuple[int, int, int] = (14, 70, 3),
                          hover: tuple[int, int, int] = (26, 130, 8),
                          pressed: tuple[int, int, int] = (6, 40, 1),
                          flash: bool = True, flash_color: str = "#ffffff",
                          flash_opacity: float = 0.14) -> HoverLift:
    """
    One-call setup that makes a small control (chip/pill/icon-button) read as
    an independent object floating over the page: a soft idle shadow, a lift
    on hover, and a tactile depress + ink flash on click. Works on any
    QAbstractButton — no subclassing required.
    """
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(idle[0])
    effect.setOffset(0, idle[2])
    effect.setColor(QColor(0, 0, 0, idle[1]))
    widget.setGraphicsEffect(effect)

    lift = HoverLift(effect, idle=idle, hover=hover, pressed=pressed, duration=140)

    if flash:
        ClickFlash(widget, color=flash_color, max_opacity=flash_opacity, duration=90)

    if hasattr(widget, "pressed"):
        widget.pressed.connect(lift.press)
        widget.released.connect(lambda: lift.release(hovered=widget.underMouse()))

    hover_filter = _HoverHandler(widget, lift)
    widget.installEventFilter(hover_filter)
    widget._hover_lift_filter = hover_filter  # keep a reference alive
    return lift


class _HoverHandler(QObject):
    """Drives HoverLift.lift()/.rest() from Qt Enter/Leave events — works on
    plain widgets with no enterEvent/leaveEvent override needed."""

    def __init__(self, host: QWidget, lift: HoverLift):
        super().__init__(host)
        self._lift = lift

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Enter:
            self._lift.lift()
        elif event.type() == QEvent.Type.Leave:
            self._lift.rest()
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  LoadingSpinner — small indeterminate rotating-arc spinner for async waits
# ─────────────────────────────────────────────────────────────────────────────

class LoadingSpinner(QWidget):
    """Indeterminate spinner. Call `.start()` when a wait begins, `.stop()` when done."""

    def __init__(self, size: int = 18, color: str | None = None, parent=None):
        super().__init__(parent)
        self._size = size
        self._color = QColor(color or P.INDIGO)
        self.setFixedSize(size, size)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def start(self):
        self.show()
        self._timer.start()

    def stop(self):
        self._timer.stop()
        self.hide()

    def _tick(self):
        self._angle = (self._angle + 8) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pen_width = max(2, self._size // 9)
        rect = QRectF(pen_width, pen_width, self._size - 2 * pen_width, self._size - 2 * pen_width)
        pen = QPen(self._color)
        pen.setWidth(pen_width)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawArc(rect, -self._angle * 16, 100 * 16)
        painter.end()


# ─────────────────────────────────────────────────────────────────────────────
#  Page-switch fade — subtle fade-in when the sidebar changes the active page
# ─────────────────────────────────────────────────────────────────────────────

def fade_in_widget(widget: QWidget, duration: int = 160):
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(duration)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.OutCubic)
    anim.finished.connect(lambda: widget.setGraphicsEffect(None))
    anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
    widget._fade_anim = anim  # keep a reference alive until it finishes
