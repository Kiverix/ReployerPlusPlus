import os
import time
from PySide6 import QtCore, QtGui, QtWidgets
from constants import ( PYGAME_AVAILABLE, PYGAME )


def show_splash(app: QtWidgets.QApplication):
    w, h = 500, 375
    pm = QtGui.QPixmap(w, h)
    pm.fill(QtGui.QColor("#1e1e1e"))

    painter = QtGui.QPainter(pm)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)

    gaq9_path = os.path.join("resources", "gaq9.png")
    sc_path = os.path.join("resources", "sourceclown.png")

    x = 30
    y = 20
    size = QtCore.QSize(200, 200)

    def draw_img(path: str, x0: int, y0: int):
        if os.path.exists(path):
            img = QtGui.QPixmap(path)
            if not img.isNull():
                img = img.scaled(size, QtCore.Qt.AspectRatioMode.KeepAspectRatio, QtCore.Qt.TransformationMode.SmoothTransformation)
                painter.drawPixmap(x0, y0, img)

    draw_img(gaq9_path, x, y)
    draw_img(sc_path, x + 240, y)

    painter.setPen(QtGui.QColor("#4fc3f7"))
    font = QtGui.QFont("Arial", 14, QtGui.QFont.Weight.Bold)
    painter.setFont(font)
    painter.drawText(QtCore.QRect(0, 240, w, 30), QtCore.Qt.AlignmentFlag.AlignCenter, "Thank you for downloading Reployer!")

    painter.setPen(QtGui.QColor("#ffffff"))
    font2 = QtGui.QFont("Arial", 11)
    painter.setFont(font2)
    painter.drawText(QtCore.QRect(0, 270, w, 22), QtCore.Qt.AlignmentFlag.AlignCenter, "Made by Kiverix (the clown)")

    painter.setPen(QtGui.QColor("#cfcfcf"))
    painter.drawText(QtCore.QRect(0, 305, w, 22), QtCore.Qt.AlignmentFlag.AlignCenter, "Loading...")

    painter.end()

    splash = QtWidgets.QSplashScreen(pm)
    splash.setWindowFlag(QtCore.Qt.WindowType.WindowStaysOnTopHint, True)
    splash.show()

    if PYGAME_AVAILABLE:
        try:
            import random
            preopen_files = ["preopen1.mp3", "preopen2.mp3", "preopen3.mp3"]
            chosen = random.choice(preopen_files)
            sound_path = os.path.join("resources", chosen)
            if os.path.exists(sound_path):
                s = PYGAME.mixer.Sound(sound_path)
                s.set_volume(0.5)
                s.play()
        except Exception:
            pass

    t_end = time.time() + 2.5
    while time.time() < t_end:
        app.processEvents()
        time.sleep(0.02)
    splash.close()