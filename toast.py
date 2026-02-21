from typing import List
from PySide6 import QtCore, QtWidgets


class Toast(QtWidgets.QWidget):
    def __init__(self, parent: QtWidgets.QWidget, message: str, kind: str = "info", duration_ms: int = 2500):
        super().__init__(parent)

        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint |
            QtCore.Qt.WindowType.Tool |
            QtCore.Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)

        colors = {
            "info": ("#232323", "#ffffff"),
            "success": ("#1f2a1f", "#b7ffb7"),
            "warn": ("#2a261f", "#ffe2a8"),
            "error": ("#2a1f1f", "#ffb7b7"),
        }
        bg, fg = colors.get(kind, colors["info"])

        frame = QtWidgets.QFrame(self)
        frame.setStyleSheet(f"""
            QFrame {{
                background: {bg};
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 10px;
            }}
            QLabel {{
                color: {fg};
                font-size: 12px;
            }}
        """)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(frame)

        inner = QtWidgets.QVBoxLayout(frame)
        inner.setContentsMargins(12, 10, 12, 10)
        label = QtWidgets.QLabel(message)
        label.setWordWrap(True)
        label.setMaximumWidth(360)
        inner.addWidget(label)

        self._anim = QtCore.QPropertyAnimation(self, b"windowOpacity", self)
        self._anim.setDuration(180)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

        QtCore.QTimer.singleShot(duration_ms, self.close_with_fade)

    def close_with_fade(self):
        anim = QtCore.QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(180)
        anim.setStartValue(self.windowOpacity())
        anim.setEndValue(0.0)
        anim.finished.connect(self.close)
        anim.start()
        self._anim = anim


class ToastManager(QtCore.QObject):
    def __init__(self, main_window: QtWidgets.QMainWindow):
        super().__init__(main_window)
        self.main_window = main_window
        self.toasts: List[Toast] = []

        # Clean up when main window is destroyed
        main_window.destroyed.connect(self._on_main_window_destroyed)

    def _main_window_alive(self) -> bool:
        try:
            return self.main_window is not None and self.main_window.isVisible()
        except RuntimeError:
            return False

    def show(self, message: str, kind: str = "info", duration_ms: int = 2500):
        if not self._main_window_alive():
            return

        toast = Toast(self.main_window, message, kind=kind, duration_ms=duration_ms)
        toast.adjustSize()
        toast.show()
        self.toasts.append(toast)
        self.reposition()

        toast.destroyed.connect(lambda *_: self._on_toast_destroyed(toast))

    def _on_main_window_destroyed(self, *_):
        # Close all toasts safely
        for t in list(self.toasts):
            try:
                t.close()
            except Exception:
                pass
        self.toasts.clear()
        self.main_window = None

    def _on_toast_destroyed(self, toast: Toast):
        self.toasts = [t for t in self.toasts if t is not toast]
        self.reposition()

    def reposition(self):
        if not self._main_window_alive():
            return

        try:
            geo = self.main_window.geometry()
            top_left = self.main_window.mapToGlobal(QtCore.QPoint(0, 0))
        except RuntimeError:
            return

        x0, y0 = top_left.x(), top_left.y()
        margin = 16
        x_right = x0 + geo.width() - margin
        y_bottom = y0 + geo.height() - margin

        y = y_bottom
        for t in self.toasts:
            if not t.isVisible():
                continue
            t.adjustSize()
            w, h = t.width(), t.height()
            y -= h
            t.move(x_right - w, y)
            y -= 8