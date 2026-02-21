
from typing import Optional
from PySide6 import QtCore, QtWidgets

from ui.profiles.profileEditorDialog import ProfileEditorDialog
from application import AppPrefs


# settings dialog
class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, prefs: Optional[AppPrefs] = None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.resize(520, 420)

        self._prefs = prefs or AppPrefs()

        layout = QtWidgets.QVBoxLayout(self)
        tabs = QtWidgets.QTabWidget()
        layout.addWidget(tabs, 1)

        tab_general = QtWidgets.QWidget()
        gform = QtWidgets.QFormLayout(tab_general)

        self.chk_tray = QtWidgets.QCheckBox("Minimize to tray instead of closing")
        self.chk_tray.setChecked(self._prefs.minimize_to_tray)

        self.chk_map_alert = QtWidgets.QCheckBox("Alert on map change")
        self.chk_map_alert.setChecked(self._prefs.alert_on_map_change)

        self.spin_player_alert = QtWidgets.QSpinBox()
        self.spin_player_alert.setRange(0, 128)
        self.spin_player_alert.setValue(self._prefs.player_alert_threshold)
        self.spin_player_alert.setToolTip("0 disables. Alerts when player count >= this value.")

        self.combo_graph_window = QtWidgets.QComboBox()
        for m in [5, 15, 60]:
            self.combo_graph_window.addItem(f"Last {m} minutes", userData=m)
        idx = max(0, self.combo_graph_window.findData(self._prefs.graph_window_minutes))
        self.combo_graph_window.setCurrentIndex(idx)

        gform.addRow(self.chk_tray)
        gform.addRow(self.chk_map_alert)
        gform.addRow("Player alert threshold", self.spin_player_alert)
        gform.addRow("Graph window", self.combo_graph_window)
        tabs.addTab(tab_general, "General")

        tab_sound = QtWidgets.QWidget()
        sform = QtWidgets.QFormLayout(tab_sound)

        self.chk_sound_enabled = QtWidgets.QCheckBox("Enable sounds")
        self.chk_sound_enabled.setChecked(self._prefs.sound.enabled)

        self.slider_vol = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.slider_vol.setRange(0, 100)
        self.slider_vol.setValue(self._prefs.sound.volume)

        self.chk_online = QtWidgets.QCheckBox("Play sound on ONLINE")
        self.chk_offline = QtWidgets.QCheckBox("Play sound on OFFLINE")
        self.chk_dl = QtWidgets.QCheckBox("Play sound on download complete")
        self.chk_alert = QtWidgets.QCheckBox("Play sound on alerts")

        self.chk_online.setChecked(self._prefs.sound.play_online)
        self.chk_offline.setChecked(self._prefs.sound.play_offline)
        self.chk_dl.setChecked(self._prefs.sound.play_download_done)
        self.chk_alert.setChecked(self._prefs.sound.play_alert)

        sform.addRow(self.chk_sound_enabled)
        sform.addRow("Volume", self.slider_vol)
        sform.addRow(self.chk_online)
        sform.addRow(self.chk_offline)
        sform.addRow(self.chk_dl)
        sform.addRow(self.chk_alert)
        tabs.addTab(tab_sound, "Sounds")

        btns = QtWidgets.QHBoxLayout()
        btns.addStretch(1)
        cancel = QtWidgets.QPushButton("Cancel")
        ok = QtWidgets.QPushButton("Save")
        ok.setDefault(True)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        layout.addLayout(btns)

        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)

    def prefs(self) -> AppPrefs:
        p = AppPrefs()
        p.minimize_to_tray = bool(self.chk_tray.isChecked())
        p.alert_on_map_change = bool(self.chk_map_alert.isChecked())
        p.player_alert_threshold = int(self.spin_player_alert.value())
        p.graph_window_minutes = int(self.combo_graph_window.currentData() or 15)

        p.sound.enabled = bool(self.chk_sound_enabled.isChecked())
        p.sound.volume = int(self.slider_vol.value())
        p.sound.play_online = bool(self.chk_online.isChecked())
        p.sound.play_offline = bool(self.chk_offline.isChecked())
        p.sound.play_download_done = bool(self.chk_dl.isChecked())
        p.sound.play_alert = bool(self.chk_alert.isChecked())
        return p