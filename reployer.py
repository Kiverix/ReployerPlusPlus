import os
import sys
import csv
import json
import time
import bz2
import urllib.request
import urllib.error
import subprocess
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Optional, Dict, List, Tuple, Any
from concurrent.futures import ThreadPoolExecutor, Future
from collections import deque
from shiboken6 import isValid
from PySide6 import QtCore, QtGui, QtWidgets
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from typing import Tuple, cast

# a2s
try:
    from a2s.info import info as a2s_info
    from a2s.players import players as a2s_players
    A2S_AVAILABLE = True
except Exception:
    A2S_AVAILABLE = False

# pygame sounds
try:
    import pygame
    pygame.mixer.init()
    pygame.mixer.set_num_channels(16)
    PYGAME_AVAILABLE = True
except Exception:
    PYGAME_AVAILABLE = False

# config
APP_NAME = "Reployer++"
APP_VERSION = "1.0"
DEFAULT_PORT = 27015
TIMEOUT = 5
UPDATE_INTERVAL = 5
SERVERS_FILENAME = "servers.json"
DOWNLOADS_ROOT = "downloads"
LOGS_ROOT = "logs"
MAX_WORKERS = 6

# game steam appids
GAME_APPIDS = {
    "tf2": 440,
    "hl2dm": 320,
    "gmod": 4000,
    "other": None,  # user supplies appid
}

GAME_LABELS = {
    "tf2": "Team Fortress 2",
    "hl2dm": "Half-Life 2: Deathmatch",
    "gmod": "Garry's Mod",
    "other": "Other",
}

# helpers
def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def now_utc_hms() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")

def fmt_hms_from_seconds(seconds: int) -> str:
    seconds = max(0, int(seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h}h {m:02d}m"
    if m > 0:
        return f"{m}m {s:02d}s"
    return f"{s}s"

def parse_address(addr: str) -> Tuple[str, int]:
    addr = (addr or "").strip()
    if not addr:
        raise ValueError("Empty address")

    if ":" in addr:
        host, port_s = addr.rsplit(":", 1)
        host = host.strip()
        port_s = port_s.strip()
        if not host:
            raise ValueError("Invalid host")
        if not port_s.isdigit():
            raise ValueError("Port must be numeric")
        port = int(port_s)
        if not (1 <= port <= 65535):
            raise ValueError("Port out of range")
        return host, port

    return addr, DEFAULT_PORT

def normalize_fastdl(url: str) -> str:
    url = (url or "").strip()
    return url.rstrip("/") if url else ""

def safe_server_folder(name: str) -> str:
    bad = '<>:"/\\|?*'
    cleaned = "".join(c for c in (name or "").strip() if c not in bad).strip()
    return cleaned or "server"

def default_appid_for_game(game: str) -> Optional[int]:
    game = (game or "").strip().lower()
    return GAME_APPIDS.get(game, None)

def game_label(game: str) -> str:
    g = (game or "").strip().lower()
    return GAME_LABELS.get(g, g.upper() if g else "Unknown")

def find_steam_executable() -> Optional[str]:
    if os.name != "nt":
        return None
    possible = [
        os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Steam", "Steam.exe"),
        os.path.join(os.environ.get("ProgramFiles", ""), "Steam", "Steam.exe"),
        os.path.join(os.environ.get("ProgramW6432", ""), "Steam", "Steam.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Steam\Steam.exe"),
        os.path.expandvars(r"%USERPROFILE%\Steam\Steam.exe"),
    ]
    for p in possible:
        if p and os.path.exists(p):
            return p
    return None

def http_open(url: str, timeout_sec: int = 15):
    req = urllib.request.Request(url, headers={"User-Agent": "Reployer/Qt (+FastDL)"})
    return urllib.request.urlopen(req, timeout=timeout_sec)

# per server csv logging
def server_log_dir(profile_name: str) -> str:
    d = os.path.join(LOGS_ROOT, safe_server_folder(profile_name))
    os.makedirs(d, exist_ok=True)
    return d

def server_csv_path(profile_name: str) -> str:
    return os.path.join(server_log_dir(profile_name), "player_log.csv")

def ensure_server_csv(csv_path: str) -> None:
    if not os.path.exists(csv_path):
        os.makedirs(os.path.dirname(csv_path), exist_ok=True)
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["UTC Timestamp", "Player Count", "Map", "Players Online"])

# persistence
def load_servers() -> List[Dict[str, Any]]:
    if not os.path.exists(SERVERS_FILENAME):
        return []
    try:
        with open(SERVERS_FILENAME, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        out: List[Dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            if "name" not in item or "address" not in item:
                continue
            out.append({
                "name": str(item.get("name", "")).strip(),
                "address": str(item.get("address", "")).strip(),
                "fastdl": str(item.get("fastdl", "")).strip(),
                "game": str(item.get("game", "tf2")).strip() or "tf2",
                "appid": item.get("appid", None),
                "fastdl_template": str(item.get("fastdl_template", "{base}/maps/{map}.bsp")).strip() or "{base}/maps/{map}.bsp",
                "auto_download_on_map_change": bool(item.get("auto_download_on_map_change", False)),
            })
        return out
    except Exception:
        return []

def save_servers(servers: List[Dict[str, Any]]) -> None:
    try:
        with open(SERVERS_FILENAME, "w", encoding="utf-8") as f:
            json.dump(servers, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

# data models
@dataclass
class ServerProfile:
    name: str
    address: Tuple[str, int]
    fastdl: str
    game: str = "tf2"
    appid: Optional[int] = None
    fastdl_template: str = "{base}/maps/{map}.bsp"
    auto_download_on_map_change: bool = False

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "ServerProfile":
        host, port = parse_address(str(d.get("address", "")).strip())
        game = str(d.get("game", "tf2")).strip() or "tf2"
        appid_val = d.get("appid", None)
        try:
            appid = int(appid_val) if appid_val is not None and str(appid_val).strip() != "" else None
        except Exception:
            appid = None
        return ServerProfile(
            name=str(d.get("name", "")).strip(),
            address=(host, port),
            fastdl=normalize_fastdl(str(d.get("fastdl", "")).strip()),
            game=game,
            appid=appid,
            fastdl_template=str(d.get("fastdl_template", "{base}/maps/{map}.bsp")).strip() or "{base}/maps/{map}.bsp",
            auto_download_on_map_change=bool(d.get("auto_download_on_map_change", False)),
        )

    def to_dict(self) -> Dict[str, Any]:
        host, port = self.address
        return {
            "name": self.name,
            "address": f"{host}:{port}",
            "fastdl": self.fastdl,
            "game": self.game,
            "appid": self.appid,
            "fastdl_template": self.fastdl_template,
            "auto_download_on_map_change": self.auto_download_on_map_change,
        }

@dataclass
class PollResult:
    ok: bool
    server_name: str
    map_name: str
    player_count: int
    max_players: int
    players: List[Dict[str, object]]  # {name, score, duration}
    error: str = ""

# toasts
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

    def show(self, message: str, kind: str = "info", duration_ms: int = 2500):
        toast = Toast(self.main_window, message, kind=kind, duration_ms=duration_ms)
        toast.adjustSize()
        toast.show()
        self.toasts.append(toast)
        self.reposition()
        toast.destroyed.connect(lambda *_: self._on_toast_destroyed(toast))

    def _on_toast_destroyed(self, toast: Toast):
        self.toasts = [t for t in self.toasts if t is not toast and not t.isHidden()]
        self.reposition()

    def reposition(self):
        if not self.main_window.isVisible():
            return

        geo = self.main_window.geometry()
        top_left = self.main_window.mapToGlobal(QtCore.QPoint(0, 0))
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

# sounds + prefs
@dataclass
class SoundSettings:
    enabled: bool = True
    volume: int = 25  # 0..100
    play_online: bool = True
    play_offline: bool = True
    play_download_done: bool = True
    play_alert: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "SoundSettings":
        return SoundSettings(
            enabled=bool(d.get("enabled", True)),
            volume=int(d.get("volume", 25)),
            play_online=bool(d.get("play_online", True)),
            play_offline=bool(d.get("play_offline", True)),
            play_download_done=bool(d.get("play_download_done", True)),
            play_alert=bool(d.get("play_alert", True)),
        )

@dataclass
class AppPrefs:
    minimize_to_tray: bool = True
    player_alert_threshold: int = 0
    alert_on_map_change: bool = True
    sound: SoundSettings = field(default_factory=SoundSettings)
    graph_window_minutes: int = 15  # 5 / 15 / 60

    def to_dict(self) -> Dict[str, Any]:
        return {
            "minimize_to_tray": self.minimize_to_tray,
            "player_alert_threshold": self.player_alert_threshold,
            "alert_on_map_change": self.alert_on_map_change,
            "sound": self.sound.to_dict(),
            "graph_window_minutes": self.graph_window_minutes,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "AppPrefs":
        sound = SoundSettings.from_dict(d.get("sound", {}) if isinstance(d.get("sound", {}), dict) else {})
        return AppPrefs(
            minimize_to_tray=bool(d.get("minimize_to_tray", True)),
            player_alert_threshold=int(d.get("player_alert_threshold", 0)),
            alert_on_map_change=bool(d.get("alert_on_map_change", True)),
            sound=sound,
            graph_window_minutes=int(d.get("graph_window_minutes", 15)),
        )

def prefs_path() -> str:
    return "prefs.json"

def load_prefs() -> AppPrefs:
    p = prefs_path()
    if not os.path.exists(p):
        return AppPrefs()
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            return AppPrefs.from_dict(d)
    except Exception:
        pass
    return AppPrefs()

def save_prefs(prefs: AppPrefs) -> None:
    try:
        with open(prefs_path(), "w", encoding="utf-8") as f:
            json.dump(prefs.to_dict(), f, indent=2, ensure_ascii=False)
    except Exception:
        pass

class SoundEngine:
    def __init__(self, prefs: AppPrefs):
        self.prefs = prefs

    def set_prefs(self, prefs: AppPrefs):
        self.prefs = prefs

    def _volume_float(self) -> float:
        v = max(0, min(100, int(self.prefs.sound.volume)))
        return v / 100.0

    def play(self, filename: str, category: str = "info"):
        if not PYGAME_AVAILABLE:
            return
        if not self.prefs.sound.enabled:
            return
        if category == "online" and not self.prefs.sound.play_online:
            return
        if category == "offline" and not self.prefs.sound.play_offline:
            return
        if category == "download" and not self.prefs.sound.play_download_done:
            return
        if category == "alert" and not self.prefs.sound.play_alert:
            return

        try:
            path = os.path.join("resources", filename)
            if os.path.exists(path):
                s = pygame.mixer.Sound(path)
                s.set_volume(self._volume_float())
                s.play()
        except Exception:
            pass

# download worker
class DownloadWorker(QtCore.QObject):
    progress = QtCore.Signal(int, int, float)  # done_bytes, total_bytes, speed_bytes_per_sec
    status = QtCore.Signal(str)
    finished = QtCore.Signal(bool, str, str)  # ok, message, bsp_path

    def __init__(self, map_name: str, fastdl_base: str, template: str, out_dir: str):
        super().__init__()
        self.map_name = map_name
        self.base = normalize_fastdl(fastdl_base)
        self.template = template.strip() or "{base}/maps/{map}.bsp"
        self.out_dir = out_dir
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def _build_url(self, ext: str) -> str:
        url = self.template.replace("{base}", self.base).replace("{map}", self.map_name)

        if ext == ".bsp":
            if url.endswith(".bsp"):
                return url
            if url.endswith(".bsp.bz2"):
                return url[:-4]
            if url.endswith("/"):
                return url + f"{self.map_name}.bsp"
            return url + ".bsp" if not url.endswith(".bsp") else url

        # ext == ".bsp.bz2"
        if url.endswith(".bsp.bz2"):
            return url
        if url.endswith(".bsp"):
            return url + ".bz2"
        if url.endswith("/"):
            return url + f"{self.map_name}.bsp.bz2"
        # make best effort
        return url + ".bsp.bz2"

    def _download_stream(self, url: str, dest_path: str) -> Tuple[bool, str]:
        last_t = time.time()
        last_bytes = 0
        done = 0

        try:
            with http_open(url, timeout_sec=15) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                self.status.emit(f"Downloading: {url}")
                with open(dest_path, "wb") as f:
                    while True:
                        if self._cancel:
                            return False, "Cancelled"
                        chunk = resp.read(64 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)

                        now = time.time()
                        if now - last_t >= 0.25:
                            dt = max(1e-6, now - last_t)
                            speed = (done - last_bytes) / dt
                            last_t = now
                            last_bytes = done
                            self.progress.emit(done, total, speed)

                self.progress.emit(done, total, 0.0)
            return True, "OK"
        except urllib.error.HTTPError as e:
            return False, f"HTTP {getattr(e, 'code', '')}".strip()
        except Exception as e:
            return False, str(e)

    @QtCore.Slot()
    def run(self):
        os.makedirs(self.out_dir, exist_ok=True)
        bsp_path = os.path.join(self.out_dir, f"{self.map_name}.bsp")
        bz2_path = os.path.join(self.out_dir, f"{self.map_name}.bsp.bz2")

        url_bsp = self._build_url(".bsp")
        url_bz2 = self._build_url(".bsp.bz2")

        self.status.emit(f"Trying .bsp: {url_bsp}")
        ok, msg = self._download_stream(url_bsp, bsp_path)
        if ok:
            self.finished.emit(True, f"Downloaded {self.map_name}.bsp", bsp_path)
            return
        if msg == "Cancelled":
            self.finished.emit(False, "Cancelled", "")
            return

        self.status.emit(f"Trying .bsp.bz2: {url_bz2}")
        ok2, msg2 = self._download_stream(url_bz2, bz2_path)
        if not ok2:
            if msg2 == "Cancelled":
                self.finished.emit(False, "Cancelled", "")
            else:
                self.finished.emit(False, "Not found on FastDL (.bsp or .bsp.bz2)", "")
            return

        if self._cancel:
            self.finished.emit(False, "Cancelled", "")
            return

        try:
            self.status.emit("Decompressing bz2…")
            with open(bz2_path, "rb") as f_in:
                data = bz2.decompress(f_in.read())
            with open(bsp_path, "wb") as f_out:
                f_out.write(data)
            self.finished.emit(True, f"Downloaded & decompressed {self.map_name}.bsp", bsp_path)
        except Exception as e:
            self.finished.emit(False, f"Downloaded .bz2 but failed to decompress: {e}", "")

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

# splash
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
    painter.drawText(QtCore.QRect(0, 305, w, 22), QtCore.Qt.AlignmentFlag.AlignCenter, "Loading…")

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
                s = pygame.mixer.Sound(sound_path)
                s.set_volume(0.5)
                s.play()
        except Exception:
            pass

    t_end = time.time() + 2.5
    while time.time() < t_end:
        app.processEvents()
        time.sleep(0.02)
    splash.close()

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