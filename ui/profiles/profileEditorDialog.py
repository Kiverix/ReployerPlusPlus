from typing import Optional
from PySide6 import  QtWidgets
from server import ServerProfile
from utils import normalize_fastdl, default_appid_for_game
from constants import (
    DEFAULT_PORT, GAME_LABELS,
)

# profile editor / manager
class ProfileEditorDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, profile: Optional[ServerProfile] = None):
        super().__init__(parent)
        self.setWindowTitle("Edit Server Profile" if profile else "Add Server Profile")
        self.setModal(True)
        self.setMinimumWidth(520)

        self._result: Optional[ServerProfile] = None
        p = profile

        layout = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        layout.addLayout(form)

        self.name_edit = QtWidgets.QLineEdit(p.name if p else "")
        self.host_edit = QtWidgets.QLineEdit((p.address[0] if p else ""))
        self.port_spin = QtWidgets.QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(p.address[1] if p else DEFAULT_PORT)

        self.game_combo = QtWidgets.QComboBox()
        for gid, label in [("tf2", GAME_LABELS["tf2"]), ("hl2dm", GAME_LABELS["hl2dm"]), ("gmod", GAME_LABELS["gmod"]), ("other", "Other…")]:
            self.game_combo.addItem(label, userData=gid)
        if p:
            idx = max(0, self.game_combo.findData(p.game))
            self.game_combo.setCurrentIndex(idx)

        self.appid_spin = QtWidgets.QSpinBox()
        self.appid_spin.setRange(1, 999999)
        self.appid_spin.setValue(int(p.appid) if (p and p.appid) else 440)
        self.appid_spin.setEnabled(False)

        self.fastdl_edit = QtWidgets.QLineEdit(p.fastdl if p else "")
        self.template_edit = QtWidgets.QLineEdit(p.fastdl_template if p else "{base}/maps/{map}.bsp")
        self.auto_dl_check = QtWidgets.QCheckBox("Auto-download map on map change")
        self.auto_dl_check.setChecked(bool(p.auto_download_on_map_change) if p else False)

        form.addRow("Name", self.name_edit)
        host_row = QtWidgets.QHBoxLayout()
        host_row.addWidget(self.host_edit, 1)
        host_row.addWidget(QtWidgets.QLabel("Port"))
        host_row.addWidget(self.port_spin)
        form.addRow("Host", host_row)
        form.addRow("Game", self.game_combo)
        form.addRow("Steam AppID (Other)", self.appid_spin)
        form.addRow("FastDL base URL", self.fastdl_edit)
        form.addRow("FastDL template", self.template_edit)
        form.addRow("", self.auto_dl_check)

        hint = QtWidgets.QLabel(
            "Template supports:\n"
            "  {base} = FastDL base URL\n"
            "  {map}  = map name\n"
            "Examples:\n"
            "  {base}/maps/{map}.bsp\n"
            "  {base}/tf/maps/{map}.bsp"
        )
        hint.setStyleSheet("color: #cfcfcf;")
        layout.addWidget(hint)

        self.status = QtWidgets.QLabel("")
        self.status.setStyleSheet("color: #ff7777;")
        layout.addWidget(self.status)

        btns = QtWidgets.QHBoxLayout()
        btns.addStretch(1)
        cancel = QtWidgets.QPushButton("Cancel")
        ok = QtWidgets.QPushButton("Save")
        ok.setDefault(True)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        layout.addLayout(btns)

        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self._on_save)
        self.game_combo.currentIndexChanged.connect(self._on_game_changed)
        self._on_game_changed()

    def _on_game_changed(self):
        gid = str(self.game_combo.currentData() or "tf2")
        if gid == "other":
            self.appid_spin.setEnabled(True)
        else:
            self.appid_spin.setEnabled(False)
            appid = default_appid_for_game(gid)
            if appid:
                self.appid_spin.setValue(appid)

    def result_profile(self) -> Optional[ServerProfile]:
        return self._result

    def _on_save(self):
        try:
            name = self.name_edit.text().strip()
            host = self.host_edit.text().strip()
            port = int(self.port_spin.value())
            game = str(self.game_combo.currentData() or "tf2").strip()
            fastdl = normalize_fastdl(self.fastdl_edit.text().strip())
            template = (self.template_edit.text().strip() or "{base}/maps/{map}.bsp")

            if not name:
                self.status.setText("Name is required.")
                return
            if not host:
                self.status.setText("Host is required.")
                return

            appid: Optional[int]
            if game == "other":
                appid = int(self.appid_spin.value())
            else:
                appid = default_appid_for_game(game)

            self._result = ServerProfile(
                name=name,
                address=(host, port),
                fastdl=fastdl,
                game=game,
                appid=appid,
                fastdl_template=template,
                auto_download_on_map_change=bool(self.auto_dl_check.isChecked()),
            )
            self.accept()
        except Exception as e:
            self.status.setText(f"Error: {e}")
