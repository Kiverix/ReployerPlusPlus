import os
import csv
import json
import urllib.request
from datetime import datetime, timezone
from typing import Optional, Dict, List, Tuple, Any
from typing import Tuple

from application import AppPrefs
from constants import (
    SERVERS_FILENAME, LOGS_ROOT, GAME_APPIDS, GAME_LABELS,
    DEFAULT_PORT,
)

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