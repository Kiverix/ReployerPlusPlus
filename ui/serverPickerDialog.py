
from typing import Optional
from PySide6 import QtWidgets

from ui.profiles.profileEditorDialog import ProfileEditorDialog
from server import ServerProfile
from utils import load_servers, save_servers, game_label, server_csv_path, default_appid_for_game

# server picker
class ServerPickerDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Choose Server")
        self.setModal(True)
        self.setMinimumWidth(640)

        self._profiles = [ServerProfile.from_dict(d) for d in load_servers()]
        self._result: Optional[ServerProfile] = None

        layout = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel("Pick a server to watch")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        layout.addWidget(title)

        tabs = QtWidgets.QTabWidget()
        layout.addWidget(tabs, 1)

        tab_load = QtWidgets.QWidget()
        l = QtWidgets.QVBoxLayout(tab_load)
        self.combo = QtWidgets.QComboBox()
        self.lbl_details = QtWidgets.QLabel("")
        self.lbl_details.setWordWrap(True)
        l.addWidget(QtWidgets.QLabel("Saved servers"))
        l.addWidget(self.combo)
        l.addWidget(self.lbl_details)
        tabs.addTab(tab_load, "Load saved")

        tab_create = QtWidgets.QWidget()
        c = QtWidgets.QVBoxLayout(tab_create)
        c.addWidget(QtWidgets.QLabel("Create a new server profile:"))
        btn_create = QtWidgets.QPushButton("Create new profile…")
        c.addWidget(btn_create)
        c.addStretch(1)
        tabs.addTab(tab_create, "Create new")

        self.status = QtWidgets.QLabel("")
        self.status.setStyleSheet("color:#ff7777;")
        layout.addWidget(self.status)

        btns = QtWidgets.QHBoxLayout()
        btns.addStretch(1)
        cancel = QtWidgets.QPushButton("Cancel")
        ok = QtWidgets.QPushButton("Continue")
        ok.setDefault(True)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        layout.addLayout(btns)

        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self._continue)
        btn_create.clicked.connect(self._create)
        self.combo.currentIndexChanged.connect(self._update_details)

        self._refresh_combo()
        if not self._profiles:
            tabs.setCurrentIndex(1)

    def _refresh_combo(self):
        self.combo.clear()
        for p in self._profiles:
            host, port = p.address
            self.combo.addItem(f"{p.name}  —  {host}:{port}", userData=p)
        if self._profiles:
            self.combo.setCurrentIndex(0)
        self._update_details()

    def _update_details(self):
        p = self.combo.currentData()
        if isinstance(p, ServerProfile):
            host, port = p.address
            appid = p.appid if p.appid else default_appid_for_game(p.game)
            self.lbl_details.setText(
                f"Game: {game_label(p.game)}\n"
                f"Address: {host}:{port}\n"
                f"AppID: {appid if appid else '(not set)'}\n"
                f"FastDL: {p.fastdl if p.fastdl else '(none)'}\n"
                f"Template: {p.fastdl_template}\n"
                f"Logs: {server_csv_path(p.name)}"
            )
        else:
            self.lbl_details.setText("")

    def _create(self):
        dlg = ProfileEditorDialog(self, None)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            p = dlg.result_profile()
            if p:
                profiles = [ServerProfile.from_dict(d) for d in load_servers()]
                profiles.append(p)
                save_servers([x.to_dict() for x in profiles])
                self._profiles = profiles
                self._refresh_combo()
                self.status.setText("Created profile.")

    def _continue(self):
        p = self.combo.currentData()
        if not isinstance(p, ServerProfile):
            self.status.setText("Pick a server profile.")
            return
        self._result = p
        self.accept()

    def profile(self) -> Optional[ServerProfile]:
        return self._result