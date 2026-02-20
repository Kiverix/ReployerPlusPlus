import os
import sys
from PySide6 import QtGui, QtWidgets
from ui.serverPickerDialog import ServerPickerDialog
from ui.mainWindow import MainWindow
from ui.splash import show_splash
from utils import load_prefs

# main
def main():
    app = QtWidgets.QApplication(sys.argv)

    try:
        ico = os.path.join("resources", "sourceclown.ico")
        if os.path.exists(ico):
            app.setWindowIcon(QtGui.QIcon(ico))
    except Exception:
        pass

    show_splash(app)

    prefs = load_prefs()

    picker = ServerPickerDialog()
    if picker.exec() != QtWidgets.QDialog.DialogCode.Accepted:
        return 0

    profile = picker.profile()
    if not profile:
        return 0

    win = MainWindow(profile, prefs)
    win.show()
    return app.exec()

if __name__ == "__main__":
    raise SystemExit(main())