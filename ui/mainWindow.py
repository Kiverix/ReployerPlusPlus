import os
import sys
import csv
import subprocess
from datetime import datetime, timezone
from typing import Optional, Dict, List, Tuple
from concurrent.futures import ThreadPoolExecutor, Future
from collections import deque
from PySide6 import QtCore, QtGui, QtWidgets
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from typing import Tuple

from sound import SoundEngine
from toast import ToastManager
from ui.profiles.profileManagerDialog import ProfileManagerDialog
from ui.profiles.settingsDialog import SettingsDialog
from downloadWorker import DownloadWorker
from polls import PollResult
from application import AppPrefs
from server import ServerProfile
from utils import safe_server_folder, load_servers, default_appid_for_game, game_label, server_csv_path, now_utc_hms, ensure_server_csv, now_utc_iso, fmt_hms_from_seconds, find_steam_executable, server_log_dir, save_prefs
from constants import (
    APP_NAME, APP_VERSION, TIMEOUT, UPDATE_INTERVAL,
    DOWNLOADS_ROOT, MAX_WORKERS, A2S_AVAILABLE, a2s_info, a2s_players
)



# main window
class MainWindow(QtWidgets.QMainWindow):
    poll_result_ready = QtCore.Signal(object)

    def __init__(self, profile: ServerProfile, prefs: AppPrefs):
        super().__init__()
        self.executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

        self.prefs = prefs
        self.sound = SoundEngine(self.prefs)
        self.toast = ToastManager(self)

        self.profile: ServerProfile = profile
        self._profiles_cache: List[ServerProfile] = [ServerProfile.from_dict(d) for d in load_servers()]

        self.query_fail_count = 0
        self.last_offline_state: Optional[bool] = None
        self.last_map: str = "Unknown"
        self.last_player_count: int = 0
        self.last_alert_player_triggered = False

        # graph history is loaded from csv each render
        self.history: List[Tuple[int, int]] = []

        self._dl_thread: Optional[QtCore.QThread] = None
        self._dl_worker: Optional[DownloadWorker] = None
        self._poll_future: Optional[Future] = None

        self.poll_result_ready.connect(self._apply_poll_result)

        self._build_ui()
        self.apply_profile(self.profile, announce=False)

        self.poll_timer = QtCore.QTimer(self)
        self.poll_timer.setInterval(int(UPDATE_INTERVAL * 1000))
        self.poll_timer.timeout.connect(self.start_poll)
        self.poll_timer.start()
        QtCore.QTimer.singleShot(50, self.start_poll)

        self._build_tray()

    # UI
    def _build_ui(self):
        host, port = self.profile.address
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION} — {self.profile.name} ({host}:{port})")
        self.resize(1550, 980)

        tb = QtWidgets.QToolBar("Main")
        tb.setMovable(False)
        self.addToolBar(tb)

        self.combo_profiles = QtWidgets.QComboBox()
        self.combo_profiles.setMinimumWidth(360)
        tb.addWidget(QtWidgets.QLabel("Server: "))
        tb.addWidget(self.combo_profiles)

        tb.addSeparator()

        self.act_refresh = QtGui.QAction("Refresh now", self)
        self.act_profiles = QtGui.QAction("Manage Profiles", self)
        self.act_settings = QtGui.QAction("Settings", self)
        tb.addAction(self.act_refresh)
        tb.addSeparator()
        tb.addAction(self.act_profiles)
        tb.addAction(self.act_settings)

        tb.addSeparator()

        self.act_connect = QtGui.QAction("Connect", self)
        self.act_sourcetv = QtGui.QAction("SourceTV", self)
        self.act_download = QtGui.QAction("Download current map", self)
        self.act_cancel_dl = QtGui.QAction("Cancel download", self)

        tb.addAction(self.act_connect)
        tb.addAction(self.act_sourcetv)
        tb.addSeparator()
        tb.addAction(self.act_download)
        tb.addAction(self.act_cancel_dl)

        self.act_cancel_dl.setEnabled(False)

        self.act_profiles.triggered.connect(self.open_profile_manager)
        self.act_settings.triggered.connect(self.open_settings)
        self.act_refresh.triggered.connect(self.start_poll)

        self.act_connect.triggered.connect(self.connect_to_server)
        self.act_sourcetv.triggered.connect(self.connect_to_sourcetv)
        self.act_download.triggered.connect(self.download_current_map)
        self.act_cancel_dl.triggered.connect(self.cancel_download)

        self.combo_profiles.currentIndexChanged.connect(self._on_profile_combo_changed)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)

        # sidebar
        side = QtWidgets.QWidget()
        side_layout = QtWidgets.QVBoxLayout(side)
        side_layout.setContentsMargins(10, 10, 10, 10)
        side_layout.setSpacing(10)

        srv_group = QtWidgets.QGroupBox("Server")
        srv_layout = QtWidgets.QVBoxLayout(srv_group)
        self.lbl_side_name = QtWidgets.QLabel("-")
        self.lbl_side_name.setStyleSheet("font-weight:700; font-size:14px;")
        self.lbl_side_addr = QtWidgets.QLabel("-")
        self.lbl_side_game = QtWidgets.QLabel("-")
        self.lbl_side_status = QtWidgets.QLabel("Status: Checking…")
        self.lbl_side_status.setStyleSheet("color:#cfcfcf;")
        srv_layout.addWidget(self.lbl_side_name)
        srv_layout.addWidget(self.lbl_side_addr)
        srv_layout.addWidget(self.lbl_side_game)
        srv_layout.addWidget(self.lbl_side_status)

        live_group = QtWidgets.QGroupBox("Live")
        live_form = QtWidgets.QFormLayout(live_group)
        self.lbl_side_map = QtWidgets.QLabel("Unknown")
        self.lbl_side_players = QtWidgets.QLabel("?/?")
        self.lbl_side_last = QtWidgets.QLabel("--")
        live_form.addRow("Map:", self.lbl_side_map)
        live_form.addRow("Players:", self.lbl_side_players)
        live_form.addRow("Last poll:", self.lbl_side_last)

        dl_group = QtWidgets.QGroupBox("Downloads")
        dl_layout = QtWidgets.QVBoxLayout(dl_group)
        self.lbl_side_fastdl = QtWidgets.QLabel("FastDL: (none)")
        self.lbl_side_fastdl.setWordWrap(True)
        btn_open_dl = QtWidgets.QPushButton("Open downloads folder")
        btn_open_dl.clicked.connect(self.open_downloads_folder)
        dl_layout.addWidget(self.lbl_side_fastdl)
        dl_layout.addWidget(btn_open_dl)

        log_group = QtWidgets.QGroupBox("Logs")
        log_layout = QtWidgets.QVBoxLayout(log_group)
        self.lbl_side_log = QtWidgets.QLabel("Log: --")
        self.lbl_side_log.setWordWrap(True)
        btn_open_log = QtWidgets.QPushButton("Open log folder")
        btn_open_log.clicked.connect(self.open_log_folder)
        log_layout.addWidget(self.lbl_side_log)
        log_layout.addWidget(btn_open_log)

        side_layout.addWidget(srv_group)
        side_layout.addWidget(live_group)
        side_layout.addWidget(dl_group)
        side_layout.addWidget(log_group)
        side_layout.addStretch(1)

        # tabs
        tabs = QtWidgets.QTabWidget()

        # overview
        tab_overview = QtWidgets.QWidget()
        ov = QtWidgets.QVBoxLayout(tab_overview)
        ov_group = QtWidgets.QGroupBox("Current Status")
        ov_form = QtWidgets.QFormLayout(ov_group)
        self.lbl_ov_server = QtWidgets.QLabel("Connecting…")
        self.lbl_ov_map = QtWidgets.QLabel("Unknown")
        self.lbl_ov_players = QtWidgets.QLabel("?/?")
        self.lbl_ov_state = QtWidgets.QLabel("Checking…")
        ov_form.addRow("Server:", self.lbl_ov_server)
        ov_form.addRow("Map:", self.lbl_ov_map)
        ov_form.addRow("Players:", self.lbl_ov_players)
        ov_form.addRow("State:", self.lbl_ov_state)
        ov.addWidget(ov_group)
        ov.addStretch(1)

        # players (model + proxy)
        tab_players = QtWidgets.QWidget()
        pl = QtWidgets.QVBoxLayout(tab_players)

        search_row = QtWidgets.QHBoxLayout()
        self.edit_player_search = QtWidgets.QLineEdit()
        self.edit_player_search.setPlaceholderText("Search players…")
        search_row.addWidget(QtWidgets.QLabel("Filter:"))
        search_row.addWidget(self.edit_player_search, 1)
        pl.addLayout(search_row)

        self.players_model = QtGui.QStandardItemModel(0, 3)
        self.players_model.setHorizontalHeaderLabels(["Name", "Score", "Time"])
        self.players_proxy = QtCore.QSortFilterProxyModel(self)
        self.players_proxy.setSourceModel(self.players_model)
        self.players_proxy.setFilterCaseSensitivity(QtCore.Qt.CaseSensitivity.CaseInsensitive)
        self.players_proxy.setFilterKeyColumn(0)

        self.players_view = QtWidgets.QTableView()
        self.players_view.setModel(self.players_proxy)
        self.players_view.setSortingEnabled(True)
        self.players_view.horizontalHeader().setStretchLastSection(True)
        self.players_view.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.players_view.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.players_view.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.players_view.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.players_view.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.players_view.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.players_view.customContextMenuRequested.connect(self._players_context_menu)
        pl.addWidget(self.players_view, 1)

        self.edit_player_search.textChanged.connect(self.players_proxy.setFilterFixedString)

        # graph
        tab_graph = QtWidgets.QWidget()
        gr = QtWidgets.QVBoxLayout(tab_graph)

        gr_top = QtWidgets.QHBoxLayout()
        self.combo_graph_window = QtWidgets.QComboBox()
        for m in [5, 15, 60]:
            self.combo_graph_window.addItem(f"Last {m} minutes", userData=m)
        idx = max(0, self.combo_graph_window.findData(self.prefs.graph_window_minutes))
        self.combo_graph_window.setCurrentIndex(idx)
        self.combo_graph_window.currentIndexChanged.connect(self._on_graph_window_changed)

        btn_export_png = QtWidgets.QPushButton("Export PNG")
        btn_export_csv = QtWidgets.QPushButton("Export CSV")
        btn_export_png.clicked.connect(self.export_graph_png)
        btn_export_csv.clicked.connect(self.export_graph_csv)

        gr_top.addWidget(QtWidgets.QLabel("Window:"))
        gr_top.addWidget(self.combo_graph_window)
        gr_top.addStretch(1)
        gr_top.addWidget(btn_export_png)
        gr_top.addWidget(btn_export_csv)
        gr.addLayout(gr_top)

        self.fig = Figure(figsize=(8, 4), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvas(self.fig)
        gr.addWidget(self.canvas, 1)

        # downloads (progress)
        tab_downloads = QtWidgets.QWidget()
        dl = QtWidgets.QVBoxLayout(tab_downloads)
        dl_box = QtWidgets.QGroupBox("FastDL")
        dl_form = QtWidgets.QFormLayout(dl_box)

        self.lbl_dl_status = QtWidgets.QLabel("Status: Waiting…")
        self.bar_dl = QtWidgets.QProgressBar()
        self.bar_dl.setRange(0, 100)
        self.bar_dl.setValue(0)
        self.lbl_dl_speed = QtWidgets.QLabel("Speed: --")
        self.lbl_dl_bytes = QtWidgets.QLabel("Downloaded: --")

        btn_dl = QtWidgets.QPushButton("Download current map")
        btn_cancel = QtWidgets.QPushButton("Cancel download")
        btn_dl.clicked.connect(self.download_current_map)
        btn_cancel.clicked.connect(self.cancel_download)

        dl_form.addRow(self.lbl_dl_status)
        dl_form.addRow(self.bar_dl)
        dl_form.addRow(self.lbl_dl_speed)
        dl_form.addRow(self.lbl_dl_bytes)
        dl_form.addRow(btn_dl, btn_cancel)

        dl.addWidget(dl_box)
        dl.addStretch(1)

        tabs.addTab(tab_overview, "Overview")
        tabs.addTab(tab_players, "Players")
        tabs.addTab(tab_graph, "Graph")
        tabs.addTab(tab_downloads, "Downloads")

        splitter.addWidget(side)
        splitter.addWidget(tabs)
        splitter.setSizes([360, 1190])
        self.setCentralWidget(splitter)

        self.status = QtWidgets.QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Initializing…")

        self.act_connect.setEnabled(False)
        self.act_sourcetv.setEnabled(False)
        self.act_download.setEnabled(False)

        self.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self and event.type() in (QtCore.QEvent.Type.Move, QtCore.QEvent.Type.Resize, QtCore.QEvent.Type.WindowStateChange):
            self.toast.reposition()
        return super().eventFilter(obj, event)

    # tray
    def _build_tray(self):
        self.tray = QtWidgets.QSystemTrayIcon(self)
        icon = self.windowIcon()
        if icon.isNull():
            icon = self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_ComputerIcon)
        self.tray.setIcon(icon)
        self.tray.setToolTip(APP_NAME)

        menu = QtWidgets.QMenu()
        act_show = menu.addAction("Show")
        act_hide = menu.addAction("Hide")
        menu.addSeparator()
        act_quit = menu.addAction("Quit")

        act_show.triggered.connect(self.showNormal)
        act_hide.triggered.connect(self.hide)
        act_quit.triggered.connect(self._quit_app)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason: QtWidgets.QSystemTrayIcon.ActivationReason):
        if reason == QtWidgets.QSystemTrayIcon.ActivationReason.Trigger:
            if self.isVisible():
                self.hide()
            else:
                self.showNormal()
                self.raise_()
                self.activateWindow()

    def _quit_app(self):
        self.tray.hide()
        QtWidgets.QApplication.quit()

    def closeEvent(self, event: QtGui.QCloseEvent):
        if self.prefs.minimize_to_tray:
            event.ignore()
            self.hide()
            self.tray.showMessage(APP_NAME, "Running in tray. Right-click tray icon to quit.", self.tray.icon(), 2000)
            return
        self._shutdown()
        event.accept()

    def _shutdown(self):
        try:
            self.poll_timer.stop()
        except Exception:
            pass
        try:
            self.executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass

    # profiles
    def _reload_profiles_cache(self):
        self._profiles_cache = [ServerProfile.from_dict(d) for d in load_servers()]

    def _refresh_profile_combo(self, keep_name: Optional[str] = None):
        self._reload_profiles_cache()
        self.combo_profiles.blockSignals(True)
        self.combo_profiles.clear()
        selected_idx = 0
        for i, p in enumerate(self._profiles_cache):
            host, port = p.address
            self.combo_profiles.addItem(f"{p.name} — {host}:{port} ({game_label(p.game)})", userData=p)
            if keep_name and p.name == keep_name:
                selected_idx = i
        self.combo_profiles.setCurrentIndex(selected_idx)
        self.combo_profiles.blockSignals(False)

    def _on_profile_combo_changed(self, _idx: int):
        p = self.combo_profiles.currentData()
        if isinstance(p, ServerProfile) and (p.name != self.profile.name or p.address != self.profile.address):
            self.apply_profile(p, announce=True)

    def open_profile_manager(self):
        dlg = ProfileManagerDialog(self)
        dlg.profile_selected.connect(lambda p: self.apply_profile(p, announce=True))
        dlg.exec()
        self._refresh_profile_combo(keep_name=self.profile.name)

    def apply_profile(self, profile: ServerProfile, announce: bool = True):
        self.profile = profile
        host, port = self.profile.address
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION} — {self.profile.name} ({host}:{port})")

        # ensure per server csv exists
        ensure_server_csv(server_csv_path(self.profile.name))

        self.lbl_side_name.setText(self.profile.name)
        self.lbl_side_addr.setText(f"{host}:{port}")
        self.lbl_side_game.setText(f"Game: {game_label(self.profile.game)}")
        self.lbl_side_fastdl.setText(f"FastDL: {self.profile.fastdl if self.profile.fastdl else '(not set)'}")
        self.lbl_side_log.setText(f"Log: {server_csv_path(self.profile.name)}")

        # reset runtime state
        self.query_fail_count = 0
        self.last_offline_state = None
        self.last_map = "Unknown"
        self.last_player_count = 0
        self.last_alert_player_triggered = False

        self.players_model.removeRows(0, self.players_model.rowCount())

        self.lbl_side_status.setText("Status: Checking…")
        self.lbl_side_map.setText("Unknown")
        self.lbl_side_players.setText("?/?")
        self.lbl_ov_state.setText("Checking…")
        self.lbl_ov_server.setText("Connecting…")
        self.lbl_ov_map.setText("Unknown")
        self.lbl_ov_players.setText("?/?")

        self.act_connect.setEnabled(False)
        self.act_sourcetv.setEnabled(False)
        self.act_download.setEnabled(False)

        self._refresh_profile_combo(keep_name=self.profile.name)

        # load graph history from this server csv and render
        self._load_history_from_csv(int(self.combo_graph_window.currentData() or self.prefs.graph_window_minutes or 15))
        self._render_graph()

        if announce:
            self.toast.show(f"Switched to {self.profile.name}", kind="info")

        self.start_poll()

    # settings
    def open_settings(self):
        dlg = SettingsDialog(self, self.prefs)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self.prefs = dlg.prefs()
            save_prefs(self.prefs)
            self.sound.set_prefs(self.prefs)
            idx = max(0, self.combo_graph_window.findData(self.prefs.graph_window_minutes))
            self.combo_graph_window.setCurrentIndex(idx)
            self.toast.show("Settings saved", kind="success")

    # polling
    def start_poll(self):
        if not A2S_AVAILABLE:
            self._set_offline_state(reason="a2s module not available")
            return
        if self._poll_future and not self._poll_future.done():
            return

        addr = self.profile.address
        self._poll_future = self.executor.submit(self._poll_server_once, addr)
        self._poll_future.add_done_callback(self._on_poll_done)

    @staticmethod
    def _poll_server_once(addr: Tuple[str, int]) -> PollResult:
        host, port = addr
        try:
            info = a2s_info((host, port), timeout=TIMEOUT, encoding='utf-8')
            players = a2s_players((host, port), timeout=TIMEOUT, encoding='utf-8')

            plist: List[Dict[str, object]] = []
            for p in players:
                name = (getattr(p, "name", "") or "").strip() or "Unnamed"
                score = int(getattr(p, "score", 0) or 0)
                dur = int(getattr(p, "duration", 0) or 0)
                plist.append({"name": name, "score": score, "duration": dur})

            server_name = (getattr(info, "server_name", "") or "").strip() or f"{host}:{port}"
            map_name = (getattr(info, "map_name", "") or "").strip() or "Unknown"
            max_players = int(getattr(info, "max_players", 0) or 0)

            return PollResult(True, server_name, map_name, len(plist), max_players, plist, "")
        except Exception as e:
            return PollResult(False, f"{host}:{port}", "Unknown", 0, 0, [], str(e))

    def _on_poll_done(self, fut: Future):
        try:
            result = fut.result()
        except Exception as e:
            result = PollResult(False, "", "Unknown", 0, 0, [], str(e))
        self.poll_result_ready.emit(result)

    @QtCore.Slot(object)
    def _apply_poll_result(self, result: PollResult):
        utc_time = now_utc_hms()
        self.lbl_side_last.setText(utc_time)

        if not result.ok:
            self.query_fail_count += 1
            offline_now = self.query_fail_count >= 5
            self.status.showMessage(f"Last update (UTC): {utc_time} | ✗ Query failed")
            if offline_now:
                self._set_offline_state(reason=result.error)
            else:
                self.lbl_side_status.setText("Status: Unstable…")
                self.lbl_ov_state.setText("UNSTABLE")
            self._update_tray_tooltip()
            return

        # success
        self.query_fail_count = 0
        self._toast_state_transition(offline_now=False)

        self.lbl_side_status.setText("Status: ONLINE")
        self.lbl_ov_state.setText("ONLINE")

        self.lbl_ov_server.setText(result.server_name)
        self.lbl_side_map.setText(result.map_name)
        self.lbl_ov_map.setText(result.map_name)

        players_text = f"{result.player_count}/{result.max_players}" if result.max_players else f"{result.player_count}"
        self.lbl_side_players.setText(players_text)
        self.lbl_ov_players.setText(players_text)

        can_launch = self._can_launch_game()
        self.act_connect.setEnabled(can_launch)
        self.act_sourcetv.setEnabled(can_launch)
        self.act_download.setEnabled(bool(self.profile.fastdl) and result.map_name != "Unknown")

        self._update_players_model(result.players)

        # log to this server's csv
        self._log_csv(result.map_name, result.player_count, result.players)

        # alerts
        self._handle_alerts(map_name=result.map_name, player_count=result.player_count)

        # auto-download on map change
        if self.profile.auto_download_on_map_change and result.map_name != "Unknown":
            if self.last_map != "Unknown" and self.last_map != result.map_name:
                self.download_current_map()

        if self.prefs.alert_on_map_change and self.last_map != "Unknown" and self.last_map != result.map_name:
            self.toast.show(f"Map changed: {self.last_map} → {result.map_name}", kind="info")

        self.last_map = result.map_name
        self.last_player_count = result.player_count

        # use latest data from csv
        self._render_graph()
        self._update_tray_tooltip()

    def _set_offline_state(self, reason: str = ""):
        self._toast_state_transition(offline_now=True)

        self.lbl_side_status.setText("Status: OFFLINE")
        self.lbl_ov_state.setText("OFFLINE")

        self.act_connect.setEnabled(False)
        self.act_sourcetv.setEnabled(False)
        self.act_download.setEnabled(False)

        if reason:
            self.status.showMessage(f"Offline: {reason}")

        self._update_tray_tooltip()

    def _toast_state_transition(self, offline_now: bool):
        if self.last_offline_state is None:
            self.last_offline_state = offline_now
            return
        if self.last_offline_state != offline_now:
            self.last_offline_state = offline_now
            if offline_now:
                self.toast.show("Server went OFFLINE", kind="error")
                self.sound.play("offline.wav", category="offline")
            else:
                self.toast.show("Server is ONLINE", kind="success")
                self.sound.play("online.wav", category="online")

    # player table
    def _update_players_model(self, players: List[Dict[str, object]]):
        def safe_int(v):
            try:
                return int(v)
            except Exception:
                return 0

        rows = sorted(
            players,
            key=lambda p: (safe_int(p.get("score")), safe_int(p.get("duration")), str(p.get("name", "")).lower()),
            reverse=True,
        )

        self.players_model.removeRows(0, self.players_model.rowCount())
        for p in rows:
            name = str(p.get("name", "Unnamed"))
            score = safe_int(p.get("score"))
            dur = safe_int(p.get("duration"))

            it_name = QtGui.QStandardItem(name)
            it_score = QtGui.QStandardItem(str(score))
            it_time = QtGui.QStandardItem(fmt_hms_from_seconds(dur))
            it_score.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
            it_time.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)

            self.players_model.appendRow([it_name, it_score, it_time])

    def _players_context_menu(self, pos: QtCore.QPoint):
        menu = QtWidgets.QMenu(self)
        act_copy_sel = menu.addAction("Copy selected as CSV")
        act_copy_all = menu.addAction("Copy all as CSV")
        act = menu.exec(self.players_view.viewport().mapToGlobal(pos))
        if act == act_copy_sel:
            self._copy_players_csv(selected_only=True)
        elif act == act_copy_all:
            self._copy_players_csv(selected_only=False)

    def _copy_players_csv(self, selected_only: bool):
        rows: List[List[str]] = [["Name", "Score", "Time"]]

        if selected_only:
            idxs = self.players_view.selectionModel().selectedRows()
            for idx in idxs:
                r = idx.row()
                name = self.players_proxy.index(r, 0).data() or ""
                score = self.players_proxy.index(r, 1).data() or ""
                tm = self.players_proxy.index(r, 2).data() or ""
                rows.append([str(name), str(score), str(tm)])
        else:
            for r in range(self.players_proxy.rowCount()):
                name = self.players_proxy.index(r, 0).data() or ""
                score = self.players_proxy.index(r, 1).data() or ""
                tm = self.players_proxy.index(r, 2).data() or ""
                rows.append([str(name), str(score), str(tm)])

        text = "\n".join([",".join([x.replace(",", " ") for x in row]) for row in rows])
        QtWidgets.QApplication.clipboard().setText(text)
        self.toast.show("Copied to clipboard", kind="success")

    # per server csv logging
    def _log_csv(self, map_name: str, player_count: int, players: List[Dict[str, object]]):
        try:
            path = server_csv_path(self.profile.name)
            ensure_server_csv(path)
            player_names = ", ".join([str(p.get("name", "")) for p in players]) if players else "None"
            with open(path, "a", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow([now_utc_iso(), player_count, map_name, player_names])
        except Exception:
            pass

    def open_log_folder(self):
        folder = server_log_dir(self.profile.name)
        try:
            if os.name == "nt":
                os.startfile(folder)  # type: ignore
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Error", f"Could not open log folder:\n{e}")

    # graph from csv
    def _load_history_from_csv(self, window_minutes: int) -> None:
        path = server_csv_path(self.profile.name)
        ensure_server_csv(path)

        cutoff_ts = datetime.now(timezone.utc).timestamp() - (window_minutes * 60)

        tail = deque(maxlen=6000)
        try:
            with open(path, "r", encoding="utf-8", newline="") as f:
                for line in f:
                    tail.append(line)
        except Exception:
            self.history = []
            return

        self.history = []
        reader = csv.DictReader(tail)
        for row in reader:
            try:
                dt = datetime.fromisoformat(row["UTC Timestamp"])
                t = int(dt.timestamp())
                if t < cutoff_ts:
                    continue
                count = int(row["Player Count"])
                self.history.append((t, count))
            except Exception:
                continue

    def _on_graph_window_changed(self):
        m = int(self.combo_graph_window.currentData() or 15)
        self.prefs.graph_window_minutes = m
        save_prefs(self.prefs)
        self._render_graph()

    def _render_graph(self):
        window_min = int(self.combo_graph_window.currentData() or self.prefs.graph_window_minutes or 15)
        self._load_history_from_csv(window_min)

        self.ax.clear()

        bg_color = "#1e1e1e"
        ax_color = "#252525"
        grid_color = "#3a3a3a"
        text_color = "#dddddd"
        line_color = "#4fc3f7"

        self.fig.patch.set_facecolor(bg_color)
        self.ax.set_facecolor(ax_color)
        self.ax.tick_params(colors=text_color)
        self.ax.xaxis.label.set_color(text_color)
        self.ax.yaxis.label.set_color(text_color)
        self.ax.title.set_color(text_color)
        for spine in self.ax.spines.values():
            spine.set_color("#444444")
        self.ax.grid(True, color=grid_color, alpha=0.6)

        if not self.history:
            self.ax.set_title("Online Players")
            self.canvas.draw_idle()
            return

        xs = [t for t, _ in self.history]
        ys = [c for _, c in self.history]

        self.ax.plot(xs, ys, marker="o", color=line_color)

        # x ticks
        n = max(1, len(xs) // 8)
        xticks = []
        xlabels = []
        for i, x in enumerate(xs):
            if i % n == 0:
                xticks.append(x)
                xlabels.append(datetime.fromtimestamp(x, tz=timezone.utc).strftime("%H:%M:%S"))
        self.ax.set_xticks(xticks)
        self.ax.set_xticklabels(xlabels, rotation=45, ha="right", color=text_color)

        max_y = max(ys) if ys else 0
        self.ax.set_ylim(0, max(32, max_y + 2))

        host, port = self.profile.address
        self.ax.set_title(f"Online Players — {host}:{port}", color=text_color)
        self.canvas.draw_idle()

    def export_graph_png(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export graph PNG", "graph.png", "PNG (*.png)")
        if not path:
            return
        try:
            self.fig.savefig(path, dpi=150, bbox_inches="tight")
            self.toast.show("Saved PNG", kind="success")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Export failed", str(e))

    def export_graph_csv(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export graph CSV", "graph.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            window_min = int(self.combo_graph_window.currentData() or self.prefs.graph_window_minutes or 15)
            self._load_history_from_csv(window_min)
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["UTC Timestamp", "Player Count"])
                for t, c in self.history:
                    dt = datetime.fromtimestamp(t, tz=timezone.utc).isoformat()
                    w.writerow([dt, c])
            self.toast.show("Saved CSV", kind="success")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Export failed", str(e))

    # alerts 
    def _handle_alerts(self, map_name: str, player_count: int):
        thr = int(self.prefs.player_alert_threshold or 0)
        if thr > 0:
            if player_count >= thr:
                if not self.last_alert_player_triggered:
                    self.last_alert_player_triggered = True
                    self.toast.show(f"Player alert: {player_count} players (≥ {thr})", kind="warn")
                    self.sound.play("information.wav", category="alert")
            else:
                self.last_alert_player_triggered = False

    # tray tooltip
    def _update_tray_tooltip(self):
        host, port = self.profile.address
        status = self.lbl_ov_state.text()
        mp = self.lbl_side_map.text()
        pl = self.lbl_side_players.text()
        self.tray.setToolTip(f"{APP_NAME}\n{self.profile.name}\n{host}:{port}\n{status}\n{mp}\n{pl}")

    # FastDL downloads
    def downloads_dir(self) -> str:
        base = os.path.join(DOWNLOADS_ROOT, safe_server_folder(self.profile.name))
        maps_dir = os.path.join(base, "maps")
        os.makedirs(maps_dir, exist_ok=True)
        return maps_dir

    def open_downloads_folder(self):
        base = os.path.join(DOWNLOADS_ROOT, safe_server_folder(self.profile.name))
        os.makedirs(base, exist_ok=True)
        try:
            if os.name == "nt":
                os.startfile(base)  # type: ignore
            elif sys.platform == "darwin":
                subprocess.Popen(["open", base])
            else:
                subprocess.Popen(["xdg-open", base])
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Error", f"Could not open downloads folder:\n{e}")

    def download_current_map(self):
        if not self.profile.fastdl:
            self.toast.show("FastDL not set", kind="warn")
            return
        map_name = self.lbl_side_map.text().strip()
        if not map_name or map_name == "Unknown":
            self.toast.show("No map info yet", kind="warn")
            return
        if self._dl_thread and self._dl_thread.isRunning():
            self.toast.show("Download already running", kind="info")
            return

        self.bar_dl.setValue(0)
        self.lbl_dl_speed.setText("Speed: --")
        self.lbl_dl_bytes.setText("Downloaded: --")
        self.lbl_dl_status.setText(f"Status: Starting download for {map_name}…")

        self._dl_thread = QtCore.QThread(self)
        self._dl_worker = DownloadWorker(
            map_name=map_name,
            fastdl_base=self.profile.fastdl,
            template=self.profile.fastdl_template,
            out_dir=self.downloads_dir(),
        )
        self._dl_worker.moveToThread(self._dl_thread)

        self._dl_thread.started.connect(self._dl_worker.run)
        self._dl_worker.status.connect(self._on_dl_status)
        self._dl_worker.progress.connect(self._on_dl_progress)
        self._dl_worker.finished.connect(self._on_dl_finished)
        self._dl_worker.finished.connect(
            lambda *_: self._dl_thread.quit() if self._dl_thread else None
        )
        self._dl_thread.finished.connect(self._cleanup_dl_thread)

        self.act_cancel_dl.setEnabled(True)
        self._dl_thread.start()
        self.toast.show(f"Downloading {map_name}…", kind="info")

    def cancel_download(self):
        if self._dl_worker:
            self._dl_worker.cancel()
            self.toast.show("Cancelling download…", kind="info")

    def _cleanup_dl_thread(self):
        self.act_cancel_dl.setEnabled(False)
        if self._dl_worker:
            self._dl_worker.deleteLater()
        if self._dl_thread:
            self._dl_thread.deleteLater()
        self._dl_worker = None
        self._dl_thread = None

    @QtCore.Slot(str)
    def _on_dl_status(self, msg: str):
        self.lbl_dl_status.setText(f"Status: {msg}")

    @QtCore.Slot(int, int, float)
    def _on_dl_progress(self, done: int, total: int, speed: float):
        if total > 0:
            pct = int((done / total) * 100)
            self.bar_dl.setValue(max(0, min(100, pct)))
        else:
            self.bar_dl.setValue(0)

        if speed > 0:
            self.lbl_dl_speed.setText(f"Speed: {speed/1024:.1f} KB/s")
        self.lbl_dl_bytes.setText(
            f"Downloaded: {done/1024/1024:.2f} MB" + (f" / {total/1024/1024:.2f} MB" if total > 0 else "")
        )

    @QtCore.Slot(bool, str, str)
    def _on_dl_finished(self, ok: bool, msg: str, _bsp_path: str):
        self.lbl_dl_status.setText(f"Status: {msg}")
        self.act_cancel_dl.setEnabled(False)

        if ok:
            self.toast.show(msg, kind="success")
            self.sound.play("information.wav", category="download")
        else:
            if msg == "Cancelled":
                self.toast.show("Download cancelled", kind="warn")
            else:
                self.toast.show(msg, kind="error")

    # game-aware launching
    def _can_launch_game(self) -> bool:
        appid = self.profile.appid if self.profile.appid else default_appid_for_game(self.profile.game)
        return bool(appid)

    def connect_to_server(self):
        self._launch_game(connect_sourcetv=False)

    def connect_to_sourcetv(self):
        self._launch_game(connect_sourcetv=True)

    def _launch_game(self, connect_sourcetv: bool):
        appid = self.profile.appid if self.profile.appid else default_appid_for_game(self.profile.game)
        if not appid:
            QtWidgets.QMessageBox.information(self, "Launch", "No AppID configured for this profile (use Other + set AppID).")
            return

        host, port = self.profile.address
        target_port = port + 1 if connect_sourcetv else port
        server = f"{host}:{target_port}"

        try:
            if os.name == "nt":
                steam = find_steam_executable()
                if not steam:
                    QtWidgets.QMessageBox.warning(self, "Steam not found", "Steam not found. Please install Steam or fix its path.")
                    return
                subprocess.Popen([steam, "-applaunch", str(appid), f"+connect {server}"])
            else:
                subprocess.Popen(["steam", "-applaunch", str(appid), f"+connect {server}"])

            self.toast.show(f"Launching {game_label(self.profile.game)} → {server}", kind="info")
            self.sound.play("join.wav", category="info")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Launch failed", str(e))
