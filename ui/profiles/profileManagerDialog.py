
import json
from typing import List
from PySide6 import QtCore, QtWidgets

from ui.profiles.profileEditorDialog import ProfileEditorDialog
from server import ServerProfile
from utils import load_servers, save_servers, game_label
from constants import ( TIMEOUT, A2S_AVAILABLE, a2s_info)

class ProfileManagerDialog(QtWidgets.QDialog):
    profile_selected = QtCore.Signal(object)  # ServerProfile

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Profiles Manager")
        self.setModal(True)
        self.resize(720, 420)

        self._profiles: List[ServerProfile] = [ServerProfile.from_dict(d) for d in load_servers()]

        layout = QtWidgets.QVBoxLayout(self)
        self.list = QtWidgets.QListWidget()
        layout.addWidget(self.list, 1)

        row = QtWidgets.QHBoxLayout()
        self.btn_add = QtWidgets.QPushButton("Add")
        self.btn_edit = QtWidgets.QPushButton("Edit")
        self.btn_del = QtWidgets.QPushButton("Delete")
        self.btn_dup = QtWidgets.QPushButton("Duplicate")
        self.btn_test = QtWidgets.QPushButton("Test A2S")
        self.btn_import = QtWidgets.QPushButton("Import…")
        self.btn_export = QtWidgets.QPushButton("Export…")
        self.btn_use = QtWidgets.QPushButton("Use Selected")
        self.btn_close = QtWidgets.QPushButton("Close")
        row.addWidget(self.btn_add)
        row.addWidget(self.btn_edit)
        row.addWidget(self.btn_del)
        row.addWidget(self.btn_dup)
        row.addStretch(1)
        row.addWidget(self.btn_test)
        row.addWidget(self.btn_import)
        row.addWidget(self.btn_export)
        row.addWidget(self.btn_use)
        row.addWidget(self.btn_close)
        layout.addLayout(row)

        self.status = QtWidgets.QLabel("")
        self.status.setStyleSheet("color:#cfcfcf;")
        layout.addWidget(self.status)

        self.btn_add.clicked.connect(self._add)
        self.btn_edit.clicked.connect(self._edit)
        self.btn_del.clicked.connect(self._delete)
        self.btn_dup.clicked.connect(self._duplicate)
        self.btn_test.clicked.connect(self._test)
        self.btn_import.clicked.connect(self._import)
        self.btn_export.clicked.connect(self._export)
        self.btn_use.clicked.connect(self._use_selected)
        self.btn_close.clicked.connect(self.accept)
        self.list.itemDoubleClicked.connect(lambda *_: self._use_selected())

        self._refresh()

    def _save(self):
        save_servers([p.to_dict() for p in self._profiles])

    def _refresh(self):
        self.list.clear()
        for p in self._profiles:
            host, port = p.address
            self.list.addItem(f"{p.name}  —  {host}:{port}  —  {game_label(p.game)}")
        if self._profiles:
            self.list.setCurrentRow(0)

    def _current_index(self) -> int:
        return self.list.currentRow()

    def _add(self):
        dlg = ProfileEditorDialog(self, None)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            p = dlg.result_profile()
            if p:
                self._profiles.append(p)
                self._save()
                self._refresh()

    def _edit(self):
        i = self._current_index()
        if i < 0 or i >= len(self._profiles):
            return
        dlg = ProfileEditorDialog(self, self._profiles[i])
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            p = dlg.result_profile()
            if p:
                self._profiles[i] = p
                self._save()
                self._refresh()
                self.list.setCurrentRow(i)

    def _delete(self):
        i = self._current_index()
        if i < 0 or i >= len(self._profiles):
            return
        p = self._profiles[i]
        if QtWidgets.QMessageBox.question(self, "Delete", f"Delete profile '{p.name}'?") != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self._profiles.pop(i)
        self._save()
        self._refresh()

    def _duplicate(self):
        i = self._current_index()
        if i < 0 or i >= len(self._profiles):
            return
        p = self._profiles[i]
        dup = ServerProfile(**p.__dict__)
        dup.name = f"{dup.name} (copy)"
        self._profiles.append(dup)
        self._save()
        self._refresh()
        self.list.setCurrentRow(len(self._profiles) - 1)

    def _import(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Import servers.json", "", "JSON files (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                raise ValueError("Invalid file (expected list).")
            imported = [ServerProfile.from_dict(d) for d in data if isinstance(d, dict)]
            self._profiles.extend(imported)
            self._save()
            self._refresh()
            self.status.setText(f"Imported {len(imported)} profile(s).")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Import failed", str(e))

    def _export(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export servers.json", "servers_export.json", "JSON files (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump([p.to_dict() for p in self._profiles], f, indent=2, ensure_ascii=False)
            self.status.setText(f"Exported to {path}")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Export failed", str(e))

    def _test(self):
        if not A2S_AVAILABLE:
            QtWidgets.QMessageBox.information(self, "A2S", "a2s module not available.")
            return
        i = self._current_index()
        if i < 0 or i >= len(self._profiles):
            return
        p = self._profiles[i]
        host, port = p.address
        try:
            info = a2s_info((host, port), timeout=TIMEOUT, encoding="utf-8")
            
            server_name = getattr(info, "server_name", "") or "(no name)"
            map_name = getattr(info, "map_name", "") or "(unknown map)"
            QtWidgets.QMessageBox.information(self, "A2S Test", f"OK\nServer: {server_name}\nMap: {map_name}")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "A2S Test", f"Failed:\n{e}")

    def _use_selected(self):
        i = self._current_index()
        if i < 0 or i >= len(self._profiles):
            return
        self.profile_selected.emit(self._profiles[i])
        self.accept()
