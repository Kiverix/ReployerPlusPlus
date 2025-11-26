import os
import csv
import time
import json
import asyncio
import threading
import subprocess
import sys
import webbrowser
from collections import deque
from datetime import datetime, timezone
from typing import Optional, Dict, Set, List, Tuple
import tkinter as tk
from tkinter import ttk, simpledialog
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

try:
    import pygame
    pygame.mixer.init()
    pygame.mixer.set_num_channels(16)
    PYGAME_AVAILABLE = True
except Exception:
    PYGAME_AVAILABLE = False

try:
    from a2s.info import info as a2s_info
    from a2s.players import players as a2s_players
    A2S_AVAILABLE = True
except Exception:
    A2S_AVAILABLE = False

try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except Exception:
    WEBSOCKETS_AVAILABLE = False

# Constants
CGE7_193: Tuple[str, int] = ("169.150.249.133", 22912)
TIMEOUT = 5
CSV_FILENAME = "player_log.csv"
VIEWS_CSV_FILENAME = "views_log.csv"
ORDINANCE_START = datetime(2025, 4, 25, 0, 0, 0, tzinfo=timezone.utc)
MAX_DATA_POINTS = 60
UPDATE_INTERVAL = 5
VIEWS_WEBSOCKET_URL = "wss://view.gaq9.com"


def center_window(window: tk.Tk, width: int, height: int) -> None:
    window.update_idletasks()
    sw = window.winfo_screenwidth()
    sh = window.winfo_screenheight()
    x = (sw // 2) - (width // 2)
    y = (sh // 2) - (height // 2)
    window.geometry(f"{width}x{height}+{x}+{y}")


class ServerMonitorApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Reployer++ - painful shit incoming")
        self.root.geometry("1500x1000")

        # Data
        self.timestamps: deque[str] = deque(maxlen=MAX_DATA_POINTS)
        self.player_counts: deque[int] = deque(maxlen=MAX_DATA_POINTS)
        self.player_list = []
        self.server_info = None
        self.current_map: Optional[str] = None

        # Map cycle
        self.sound_played_minute: Optional[int] = None
        self.last_time_sound_minute: Optional[int] = None

        # Views
        self.last_view_id: Optional[int] = None
        self.websocket_running = True

        # Dev mode
        self.dev_mode = False
        self.dev_buttons_frame = None

        # Session numbering + names
        self.player_numbers: Dict[int, int] = {}
        self.assigned_numbers: Set[int] = set()
        self.player_original_names: Dict[int, str] = {}
        self.number_reset_hour = datetime.utcnow().hour
        self.pending_name_assignments: Dict[int, int] = {}  # join_epoch -> after_id

        # Query failures
        self.query_fail_count = 0
        self._last_online_state: Optional[bool] = None

        # UI
        self.setup_theme()
        self.create_custom_title_bar()
        self.init_csv()
        self.init_views_csv()
        self.load_existing_data()
        self.create_widgets()

        # Background
        self.running = True
        self.start_monitoring()
        if WEBSOCKETS_AVAILABLE:
            self.start_websocket_monitor()
        self.play_sound("open.wav")
        self.update_map_display()
        self.start_nocontrol_timer()

    # Title bar
    def create_custom_title_bar(self) -> None:
        self.title_bar = tk.Frame(self.root, bg="#232323", height=32)
        self.title_bar.pack(fill=tk.X, side=tk.TOP)
        self.title_bar.bind("<Button-1>", self._start_move)
        self.title_bar.bind("<B1-Motion>", self._on_move)

        tk.Label(self.title_bar, text="Reployer v3.2 - With Love, by Kiverix",
                 bg="#232323", fg="#4fc3f7", font=("Arial", 12, "bold")).pack(side=tk.LEFT, padx=10)

        # Dev mode button
        self.dev_mode_btn = tk.Button(self.title_bar, text="●", bg="#232323", fg="#00ff00", font=("Arial", 16, "bold"),
                                      bd=0, relief=tk.FLAT, activebackground="#3d3d3d", activeforeground="#00ff00",
                                      command=self.toggle_dev_mode, cursor="hand2")
        self.dev_mode_btn.pack(side=tk.LEFT, padx=(10, 0))
        self.dev_mode_btn.bind("<Enter>", self.play_hover_sound)

        tk.Button(self.title_bar, text="✕", bg="#232323", fg="#ff5555", font=("Arial", 12, "bold"),
                  bd=0, relief=tk.FLAT, activebackground="#3d3d3d", activeforeground="#ff5555",
                  command=self.on_close, cursor="hand2").pack(side=tk.RIGHT, padx=(0, 10))

        btn_min = tk.Button(self.title_bar, text="━", bg="#232323", fg="#4fc3f7", font=("Arial", 12, "bold"),
                            bd=0, relief=tk.FLAT, activebackground="#3d3d3d", activeforeground="#4fc3f7",
                            command=self.minimize_window, cursor="hand2")
        btn_min.pack(side=tk.RIGHT, padx=(0, 0))
        btn_min.bind("<Enter>", self.play_hover_sound)

    def minimize_window(self) -> None:
        self.root.update_idletasks()
        self.root.iconify()

    def _start_move(self, event) -> None:
        self._drag_start_x = event.x
        self._drag_start_y = event.y

    def _on_move(self, event) -> None:
        x = self.root.winfo_x() + event.x - self._drag_start_x
        y = self.root.winfo_y() + event.y - self._drag_start_y
        self.root.geometry(f"+{x}+{y}")

    def toggle_dev_mode(self) -> None:
        self.dev_mode = not self.dev_mode
        if self.dev_mode:
            self.dev_mode_btn.config(fg="#ff0000")  # Red when active
            self._create_dev_buttons()
        else:
            self.dev_mode_btn.config(fg="#00ff00")  # Green when inactive
            self._destroy_dev_buttons()
        self.play_sound("information.wav")

    # Theme
    def setup_theme(self) -> None:
        self.theme = {
            "bg": "#2d2d2d", "fg": "#ffffff", "frame": "#3d3d3d",
            "graph_bg": "#1e1e1e", "graph_fg": "#ffffff", "graph_grid": "#4d4d4d",
            "plot": "#4fc3f7", "listbox_bg": "#3d3d3d", "listbox_fg": "#ffffff",
            "select_bg": "#4fc3f7", "select_fg": "#ffffff",
            "status_online": "green", "status_restart1": "blue", "status_restart2": "gold",
            "button_bg": "#3d3d3d", "button_fg": "#ffffff",
            "views_bg": "#3d3d3d", "views_fg": "#4fc3f7",
        }
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background=self.theme["bg"], foreground=self.theme["fg"])
        style.configure("TFrame", background=self.theme["bg"])
        style.configure("TLabel", background=self.theme["bg"], foreground=self.theme["fg"])
        style.configure("TButton", background=self.theme["button_bg"], foreground=self.theme["button_fg"])
        self.root.configure(bg=self.theme["bg"])

    # CSV
    def init_csv(self) -> None:
        if not os.path.exists(CSV_FILENAME):
            with open(CSV_FILENAME, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(["UTC Timestamp", "Player Count", "Map", "Players Online"])

    def init_views_csv(self) -> None:
        if not os.path.exists(VIEWS_CSV_FILENAME):
            with open(VIEWS_CSV_FILENAME, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(["UTC Timestamp", "View ID", "View Timestamp"])

    def load_existing_data(self) -> None:
        if not os.path.exists(CSV_FILENAME):
            return
        try:
            with open(CSV_FILENAME, "r", newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            for row in rows[-MAX_DATA_POINTS:]:
                try:
                    dt = datetime.fromisoformat(row["UTC Timestamp"].replace("Z", "+00:00"))
                    self.timestamps.append(dt.strftime("%H:%M:%S"))
                    self.player_counts.append(int(row["Player Count"]))
                except Exception:
                    continue
        except Exception:
            pass

    # Widgets
    def create_widgets(self) -> None:
        main = ttk.Frame(self.root)
        main.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        left = ttk.Frame(main)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        right = ttk.Frame(main)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        self._create_server_info_frame(left)
        self._create_views_frame(left)
        self._create_player_list_frame(left)
        self._create_graph_frame(right)
        self._create_action_buttons()
        self._create_status_bars()
        self._create_debug_bar()

    def _create_views_frame(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="CGE7-193 Diet View Monitor", padding=10)
        frame.pack(fill=tk.X, padx=5, pady=5)

        self.views_label = tk.Label(frame, text="Current View ID: Waiting...", font=("Arial", 10, "bold"),
                                    bg=self.theme["views_bg"], fg=self.theme["views_fg"])
        self.views_label.pack(anchor=tk.W)

        self.last_view_time_label = tk.Label(frame, text="Last View Time: --", font=("Arial", 9), bg=self.theme["bg"])
        self.last_view_time_label.pack(anchor=tk.W)

        status_text = "Connecting..." if WEBSOCKETS_AVAILABLE else "WebSocket module not available"
        self.views_status = ttk.Label(frame, text=f"Status: {status_text}")
        self.views_status.pack(anchor=tk.W)

    def _create_server_info_frame(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="CGE7-193 Information", padding=10)
        frame.pack(fill=tk.X, padx=5, pady=5)

        self.server_name_label = ttk.Label(frame, text="Server Name: Testing connection...")
        self.server_name_label.pack(anchor=tk.W)

        self.server_map_label = ttk.Label(frame, text="Current Map: Unknown")
        self.server_map_label.pack(anchor=tk.W)

        self.player_count_label = ttk.Label(frame, text="Players: ?/?")
        self.player_count_label.pack(anchor=tk.W)

        ttk.Separator(frame, orient="horizontal").pack(fill=tk.X, pady=5)

        self.current_map_cycle_label = ttk.Label(frame, text="Current Map Cycle: Loading...", font=("Arial", 10, "bold"))
        self.current_map_cycle_label.pack(anchor=tk.W)

        self.adjacent_maps_label = ttk.Label(frame, text="Previous: Loading... | Next: Loading...", font=("Arial", 9))
        self.adjacent_maps_label.pack(anchor=tk.W)

        self.countdown_label = ttk.Label(frame, text="Next cycle in: --:--", font=("Arial", 9, "bold"))
        self.countdown_label.pack(anchor=tk.W)

        self.time_label = ttk.Label(frame, text="UTC: --:--:-- | Local: --:--:--", font=("Arial", 9))
        self.time_label.pack(anchor=tk.W)

        self.restart_status_label = ttk.Label(frame, text="Server Status: ONLINE", font=("Arial", 10, "bold"),
                                              foreground=self.theme["status_online"])
        self.restart_status_label.pack(anchor=tk.W)

    def _create_player_list_frame(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Online Players", padding=10)
        frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.player_listbox = tk.Listbox(
            frame, bg=self.theme["listbox_bg"], fg=self.theme["listbox_fg"],
            selectbackground=self.theme["select_bg"], selectforeground=self.theme["select_fg"]
        )
        self.player_listbox.pack(fill=tk.BOTH, expand=True)

    def _create_graph_frame(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Player Count History", padding=10)
        frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.fig = Figure(figsize=(8, 4), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self._apply_graph_theme()

        self.canvas = FigureCanvasTkAgg(self.fig, master=frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    def _apply_graph_theme(self) -> None:
        self.fig.set_facecolor(self.theme["graph_bg"])
        self.ax.set_facecolor(self.theme["graph_bg"])
        self.ax.tick_params(colors=self.theme["graph_fg"])
        self.ax.grid(True, color=self.theme["graph_grid"])

    def _create_action_buttons(self) -> None:
        frame = ttk.Frame(self.root)
        frame.pack(fill=tk.X, padx=10, pady=5)

        self.cge_button = ttk.Button(frame, text="Connect to CGE7-193", command=self.connect_to_cge, state=tk.DISABLED)
        self.cge_button.pack(side=tk.LEFT, padx=5)
        self.cge_button.bind("<Enter>", self.play_hover_sound)

        self.sourceTV_button = ttk.Button(frame, text="Connect to SourceTV", command=self.connect_to_sourceTV, state=tk.DISABLED)
        self.sourceTV_button.pack(side=tk.LEFT, padx=5)
        self.sourceTV_button.bind("<Enter>", self.play_hover_sound)

    def _create_dev_buttons(self) -> None:
        if self.dev_buttons_frame is not None:
            return

        self.dev_buttons_frame = ttk.LabelFrame(self.root, text="Developer Mode - Testing Panel", padding=10)
        # Pack without relying on status bar existence
        self.dev_buttons_frame.pack(fill=tk.X, padx=10, pady=5)

        # Sound test buttons
        sound_frame = ttk.Frame(self.dev_buttons_frame)
        sound_frame.pack(fill=tk.X, pady=5)
        ttk.Label(sound_frame, text="Sound Tests:", font=("Arial", 10, "bold")).pack(anchor=tk.W)

        sound_row1 = ttk.Frame(sound_frame)
        sound_row1.pack(fill=tk.X, pady=2)
        sound_row2 = ttk.Frame(sound_frame)
        sound_row2.pack(fill=tk.X, pady=2)
        sound_row3 = ttk.Frame(sound_frame)
        sound_row3.pack(fill=tk.X, pady=2)

        sounds = [
            "open.wav", "close.wav", "join.wav", "hover.wav", "information.wav",
            "offline.wav", "online.wav", "new_cycle.wav", "thirty.wav", "fifteen.wav",
            "five.wav", "ordinance.wav", "ord_cry.wav", "ord_err.wav", "ord_ren.wav",
            "ord_mapchange.wav", "new_view.wav"
        ]

        for i, sound in enumerate(sounds):
            if i < 6:
                parent = sound_row1
            elif i < 12:
                parent = sound_row2
            else:
                parent = sound_row3

            btn = ttk.Button(parent, text=sound.replace(".wav", ""),
                             command=lambda s=sound: self.play_sound(s))
            btn.pack(side=tk.LEFT, padx=2, pady=1)

        # Function test buttons
        func_frame = ttk.Frame(self.dev_buttons_frame)
        func_frame.pack(fill=tk.X, pady=5)
        ttk.Label(func_frame, text="Function Tests:", font=("Arial", 10, "bold")).pack(anchor=tk.W)

        func_row = ttk.Frame(func_frame)
        func_row.pack(fill=tk.X, pady=2)

        ttk.Button(func_row, text="Test Server Query",
                   command=self._test_server_query).pack(side=tk.LEFT, padx=2)
        ttk.Button(func_row, text="Test WebSocket",
                   command=self._test_websocket).pack(side=tk.LEFT, padx=2)
        ttk.Button(func_row, text="Simulate New View",
                   command=self._simulate_new_view).pack(side=tk.LEFT, padx=2)
        ttk.Button(func_row, text="Force Graph Update",
                   command=self._update_graph).pack(side=tk.LEFT, padx=2)
        ttk.Button(func_row, text="Test Map Change",
                   command=self._test_map_change).pack(side=tk.LEFT, padx=2)

    def _destroy_dev_buttons(self) -> None:
        if self.dev_buttons_frame is not None:
            self.dev_buttons_frame.destroy()
            self.dev_buttons_frame = None

    # Dev test functions
    def _test_server_query(self) -> None:
        self.status_var.set("Testing server query...")
        threading.Thread(target=self._do_test_server_query, daemon=True).start()

    def _do_test_server_query(self) -> None:
        info, count, players = self.get_server_info()
        result = f"Query result: {count} players, Map: {info.map_name if info else 'Unknown'}"
        self.root.after(0, self.status_var.set, result)

    def _test_websocket(self) -> None:
        self.status_var.set("WebSocket status: " + ("Available" if WEBSOCKETS_AVAILABLE else "Not Available"))

    def _simulate_new_view(self) -> None:
        import random
        fake_view_id = random.randint(1000, 9999)
        fake_timestamp = datetime.now().timestamp()
        self._log_new_view(fake_view_id, fake_timestamp)
        self.play_sound("new_view.wav")
        self.status_var.set(f"Simulated new view: ID {fake_view_id}")

    def _test_map_change(self) -> None:
        test_maps = ["ordinance", "ord_cry", "ord_err", "ord_ren", "2fort", "dustbowl"]
        import random
        test_map = random.choice(test_maps)
        old_map = self.current_map
        self._check_map_change(test_map)
        self.status_var.set(f"Simulated map change: {old_map} -> {test_map}")

    def _create_status_bars(self) -> None:
        self.status_var = tk.StringVar(value="Initializing...")
        ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.CENTER).pack(fill=tk.X, padx=10, pady=5)

        self.ordinance_var = tk.StringVar(value="Calculating time since ordinance start...")
        ttk.Label(self.root, textvariable=self.ordinance_var, relief=tk.SUNKEN, anchor=tk.CENTER).pack(fill=tk.X, padx=10, pady=(0, 5))

    def _create_debug_bar(self) -> None:
        frame = ttk.Frame(self.root)
        frame.pack(fill=tk.X, padx=10, pady=5)

        ip_text = f"Server: {CGE7_193[0]}:{CGE7_193[1]}"
        ip_label = tk.Label(frame, text=ip_text, fg="#4fc3f7", cursor="hand2", bg=self.theme["bg"])
        ip_label.pack(side=tk.RIGHT)

        def _copy_ip(_=None) -> None:
            self.root.clipboard_clear()
            self.root.clipboard_append(f"{CGE7_193[0]}:{CGE7_193[1]}")
            self.status_var.set("Server IP copied to clipboard!")
            self.play_sound("information.wav")

        ip_label.bind("<Button-1>", _copy_ip)

        def _link(lbl_text: str, url: str, color: str, padx=(0, 0)):
            lbl = tk.Label(frame, text=lbl_text, fg=color, bg=self.theme["bg"], font=("Arial", 9), cursor="hand2")
            lbl.pack(side=tk.LEFT, padx=padx)

            def _open(_=None):
                try:
                    webbrowser.open(url)
                    self.play_sound("information.wav")
                except Exception:
                    pass

            lbl.bind("<Button-1>", _open)

        _link("Go to gaq9.com", "https://gaq9.com", "purple", (0, 10))
        _link("Join Anomalous Materials on Discord", "https://discord.gg/anomidae", "beige")
        _link("Subscribe to my Youtube", "https://www.youtube.com/@kiverix", "red", (10, 0))

    # Sounds
    def start_nocontrol_timer(self) -> None:
        self._nocontrol_timer_running = True
        self._nocontrol_timer()

    def _nocontrol_timer(self) -> None:
        if not getattr(self, "_nocontrol_timer_running", False):
            return
        self.play_nocontrol_sound()
        self.root.after(51280, self._nocontrol_timer)

    def play_nocontrol_sound(self) -> None:
        if not PYGAME_AVAILABLE:
            return
        try:
            sound_path = os.path.join("resources", "nocontrol.wav")
            if os.path.exists(sound_path):
                s = pygame.mixer.Sound(sound_path)
                s.set_volume(0.2)
                s.play()
        except Exception:
            pass

    def play_hover_sound(self, _event=None) -> None:
        if not PYGAME_AVAILABLE:
            return
        try:
            sound_path = os.path.join("resources", "hover.wav")
            if os.path.exists(sound_path):
                s = pygame.mixer.Sound(sound_path)
                s.set_volume(0.25)
                s.play()
        except Exception:
            pass

    def play_sound(self, sound_file: str) -> None:
        if not PYGAME_AVAILABLE:
            return
        try:
            sound_path = os.path.join("resources", sound_file)
            if os.path.exists(sound_path):
                s = pygame.mixer.Sound(sound_path)
                if sound_file in ("join.wav", "information.wav"):
                    s.set_volume(0.25)
                elif sound_file == "new_view.wav":
                    s.set_volume(0.50)
                s.play()
        except Exception:
            pass

    # Server querying
    def get_server_info(self):
        if not A2S_AVAILABLE:
            return None, 0, []
        try:
            info = a2s_info(CGE7_193, timeout=TIMEOUT)
            players = a2s_players(CGE7_193, timeout=TIMEOUT)
            return info, len(players), players
        except Exception:
            return None, 0, []

    def start_monitoring(self) -> None:
        threading.Thread(target=self._update_loop, daemon=True).start()

    def _update_loop(self) -> None:
        while self.running:
            try:
                self.update_server_info()
            except Exception:
                pass
            time.sleep(UPDATE_INTERVAL)

    def update_server_info(self) -> None:
        info, player_count, players = self.get_server_info()
        self._reset_player_numbers_if_new_hour()

        if info is None:
            self.query_fail_count += 1
            query_status = "✗ Query failed"
            info = self.server_info or None
            player_count = (self.player_counts[-1] if self.player_counts else 0)
            players = (self.player_list or [])
        else:
            self.query_fail_count = 0
            query_status = "✓ Query successful"
            self.server_info = info
            self.player_list = players

        offline_now = self.query_fail_count >= 5 or (self.query_fail_count > 0 and not info)

        if self._last_online_state is None:
            self._last_online_state = offline_now
        elif self._last_online_state != offline_now:
            self.play_sound("offline.wav" if offline_now else "online.wav")
            self._last_online_state = offline_now

        if offline_now:
            self.restart_status_label.config(text="Server Status: OFFLINE", foreground="red")
            self.cge_button.config(state=tk.DISABLED)
            self.sourceTV_button.config(state=tk.DISABLED)
        elif info:
            current_map = self._update_server_display(info, player_count)
            self._update_button_states(current_map)

        self._update_player_list(players)
        self._log_and_update_graph(info.map_name if info else "Unknown", player_count, players)

        if not hasattr(self, "_ordinance_timer_started"):
            self._ordinance_timer_started = True
            self.update_ordinance_time()

        current_time = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self.status_var.set(f"Last update (UTC): {current_time} | {query_status}")

    def _update_server_display(self, info, player_count: int) -> str:
        current_map = "Unknown"
        if info:
            self.server_name_label.config(text=f"Server Name: {info.server_name}")
            self.server_map_label.config(text=f"Current Map: {info.map_name}")
            self.player_count_label.config(text=f"Players: {player_count}/{info.max_players}")
            current_map = info.map_name
            self._check_map_change(current_map)
        else:
            self.server_name_label.config(text="Server Name: Unknown")
            self.server_map_label.config(text="Current Map: Unknown")
            self.player_count_label.config(text="Players: ?/?")
            self.cge_button.config(state=tk.DISABLED)
            self.sourceTV_button.config(state=tk.DISABLED)
        return current_map

    def _update_button_states(self, current_map: str) -> None:
        if self.dev_mode:
            # In dev mode, always enable both buttons
            self.cge_button.config(state=tk.NORMAL)
            self.sourceTV_button.config(state=tk.NORMAL)
        else:
            self.cge_button.config(state=(tk.NORMAL if current_map.lower() == "2fort" else tk.DISABLED))
            excluded_maps = {"mazemazemazemaze", "kurt", "ask", "askask"}
            self.sourceTV_button.config(state=(tk.NORMAL if current_map.lower() not in excluded_maps else tk.DISABLED))

    # Player list / numbering / names
    def _reset_player_numbers_if_new_hour(self) -> None:
        cur_hour = datetime.utcnow().hour
        if cur_hour != self.number_reset_hour:
            self.player_numbers.clear()
            self.assigned_numbers.clear()
            self.player_original_names.clear()
            for after_id in list(self.pending_name_assignments.values()):
                try:
                    self.root.after_cancel(after_id)
                except Exception:
                    pass
            self.pending_name_assignments.clear()
            self.number_reset_hour = cur_hour

    @staticmethod
    def _estimate_join_epoch(duration_seconds: int) -> int:
        now = datetime.now(timezone.utc)
        return int(now.timestamp() - max(0, int(duration_seconds)))

    def _find_existing_epoch(self, candidate_epoch: int, tolerance: int = 5) -> Optional[int]:
        for existing in self.player_numbers.keys():
            if abs(existing - candidate_epoch) <= tolerance:
                return existing
        return None

    def _get_or_assign_number(self, join_epoch: int, max_slots: int = 17) -> int:
        if join_epoch in self.player_numbers:
            return self.player_numbers[join_epoch]
        for n in range(1, max_slots + 1):
            if n not in self.assigned_numbers:
                self.player_numbers[join_epoch] = n
                self.assigned_numbers.add(n)
                return n
        n = max_slots
        self.player_numbers[join_epoch] = n
        self.assigned_numbers.add(n)
        return n

    def _update_player_list(self, players) -> None:
        self.player_listbox.delete(0, tk.END)

        if not players:
            self.player_numbers.clear()
            self.assigned_numbers.clear()
            self.player_original_names.clear()
            for after_id in list(self.pending_name_assignments.values()):
                try:
                    self.root.after_cancel(after_id)
                except Exception:
                    pass
            self.pending_name_assignments.clear()
            self.player_listbox.insert(tk.END, "No players online")
            return

        max_slots = 17
        try:
            if self.server_info and getattr(self.server_info, "max_players", None):
                max_slots = int(self.server_info.max_players)
        except Exception:
            pass

        current_epochs: List[Tuple[int, object]] = []
        for p in players:
            dur = int(getattr(p, "duration", 0) or 0)
            candidate = self._estimate_join_epoch(dur)
            existing = self._find_existing_epoch(candidate, tolerance=5)
            join_epoch = existing if existing is not None else candidate
            current_epochs.append((join_epoch, p))

        current_epoch_set = {e for e, _ in current_epochs}
        stale = [e for e in list(self.player_numbers.keys()) if e not in current_epoch_set]
        for e in stale:
            n = self.player_numbers.pop(e, None)
            if n is not None and n in self.assigned_numbers:
                self.assigned_numbers.remove(n)
            self.player_original_names.pop(e, None)
            if e in self.pending_name_assignments:
                try:
                    self.root.after_cancel(self.pending_name_assignments.pop(e))
                except Exception:
                    self.pending_name_assignments.pop(e, None)

        entries: List[Tuple[int, str]] = []
        for join_epoch, p in current_epochs:
            num = self._get_or_assign_number(join_epoch, max_slots=max_slots)
            current_name = p.name.strip() if getattr(p, "name", None) else "connecting..."

            # Delay assigning original name by 1s after first non-empty
            if join_epoch not in self.player_original_names:
                if current_name != "connecting..." and join_epoch not in self.pending_name_assignments:
                    after_id = self.root.after(1000, self._finalize_name_assignment, join_epoch, current_name)
                    self.pending_name_assignments[join_epoch] = after_id

            original_name = self.player_original_names.get(join_epoch, current_name)

            dur = int(getattr(p, "duration", 0) or 0)
            hours = dur // 3600
            minutes = (dur % 3600) // 60
            playtime = f" ({hours}h {minutes}m)" if dur > 0 else ""

            entries.append((num, f"[{num:02d}] {original_name}{playtime}"))

        for _, line in sorted(entries, key=lambda t: t[0]):
            self.player_listbox.insert(tk.END, line)

    def _finalize_name_assignment(self, join_epoch: int, name: str) -> None:
        if join_epoch not in self.player_original_names:
            self.player_original_names[join_epoch] = name
        self.pending_name_assignments.pop(join_epoch, None)

    # Graph
    def _log_and_update_graph(self, current_map: str, player_count: int, players) -> None:
        current_time = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self.timestamps.append(current_time)
        self.player_counts.append(player_count)

        try:
            with open(CSV_FILENAME, "a", newline="", encoding="utf-8") as f:
                player_names = ", ".join([p.name for p in players]) if players else "None"
                csv.writer(f).writerow([datetime.now(timezone.utc).isoformat(), player_count, current_map, player_names])
        except Exception:
            pass

        self._update_graph()

    def _update_graph(self) -> None:
        if not self.timestamps:
            return
        self.ax.clear()
        xs = list(range(len(self.timestamps)))
        self.ax.plot(xs, list(self.player_counts), color=self.theme["plot"], marker="o")
        n = max(1, len(xs) // 10)
        labels = [self.timestamps[i] if i % n == 0 else "" for i in xs]
        self.ax.set_xticks(xs)
        self.ax.set_xticklabels(labels, rotation=45, ha="right")
        self.ax.set_ylim(0, 30)
        self.ax.set_yticks(list(range(17)))
        self.ax.set_title(f"Online Players - {CGE7_193[0]}:{CGE7_193[1]}", color=self.theme["graph_fg"])
        self._apply_graph_theme()
        self.canvas.draw()

    # Map schedule & status
    @staticmethod
    def _map_by_utc_hour(hour: Optional[int] = None) -> str:
        if hour is None:
            hour = datetime.utcnow().hour
        schedule = {
            0: "askask", 1: "ask", 2: "ask", 3: "askask",
            4: "ask", 5: "dustbowl", 6: "askask", 7: "ask",
            8: "ask", 9: "askask", 10: "ask", 11: "dustbowl",
            12: "askask", 13: "ask", 14: "ask", 15: "askask",
            16: "ask", 17: "dustbowl", 18: "askask", 19: "ask",
            20: "dustbowl", 21: "askask", 22: "ask", 23: "dustbowl",
        }
        return schedule.get(hour, "unknown")

    def _adjacent_maps_and_countdown(self):
        now = datetime.utcnow()
        ch, cm, cs = now.hour, now.minute, now.second
        prev_map = self._map_by_utc_hour((ch - 1) % 24)
        next_map = self._map_by_utc_hour((ch + 1) % 24)
        secs = (59 - cs) % 60
        mins = (59 - cm) % 60
        return prev_map, next_map, mins, secs

    def update_map_display(self) -> None:
        utc_now = datetime.utcnow()
        local_now = datetime.now()

        self.time_label.config(text=f"UTC: {utc_now.strftime('%H:%M:%S')} | Local: {local_now.strftime('%H:%M:%S')}")

        current_map = self._map_by_utc_hour()
        prev_map, next_map, mins_left, secs_left = self._adjacent_maps_and_countdown()

        cm, cs = utc_now.minute, utc_now.second
        restart_type = None
        if cm == 59 and cs >= 10:
            restart_status = "FIRST RESTART"
            status_color = self.theme["status_restart1"]
            restart_type = "FIRST"
        elif cm == 1 and cs <= 30:
            restart_status = "SECOND RESTART"
            status_color = self.theme["status_restart2"]
            restart_type = "SECOND"
        else:
            restart_status = "ONLINE"
            status_color = self.theme["status_online"]
            restart_type = None

        if not hasattr(self, "_last_restart_type"):
            self._last_restart_type = None
        if restart_type and self._last_restart_type != restart_type:
            self.play_sound("information.wav")
            self._last_restart_type = restart_type
        elif restart_type is None:
            self._last_restart_type = None

        self.current_map_cycle_label.config(text=f"Current Map Cycle: {current_map}")
        self.adjacent_maps_label.config(text=f"Previous: {prev_map} | Next: {next_map}")
        self.countdown_label.config(text=f"Next cycle in: {mins_left:02d}m {secs_left:02d}s")

        if self.query_fail_count >= 15:
            self.restart_status_label.config(text="Server Status: OFFLINE", foreground="red")
        else:
            self.restart_status_label.config(text=f"Server Status: {restart_status}", foreground=status_color)

        self._handle_time_warning_sounds(utc_now)

        if utc_now.minute == 59 and utc_now.second == 0:
            if self.sound_played_minute != utc_now.hour:
                self.play_sound("new_cycle.wav")
                self.sound_played_minute = utc_now.hour
        elif utc_now.minute != 59:
            self.sound_played_minute = None

        self.root.after(50, self.update_map_display)

    def _handle_time_warning_sounds(self, utc_now: datetime) -> None:
        cm, cs = utc_now.minute, utc_now.second
        if cs == 0:
            minute_sounds = {30: "thirty.wav", 45: "fifteen.wav", 55: "five.wav"}
            key = minute_sounds.get(cm)
            if key and self.last_time_sound_minute != cm:
                self.play_sound(key)
                self.last_time_sound_minute = cm
            elif cm not in minute_sounds:
                self.last_time_sound_minute = None

    def _check_map_change(self, new_map: str) -> None:
        if self.current_map is not None and self.current_map != new_map:
            if new_map == "ordinance":
                self.play_sound("ordinance.wav")
            elif new_map.startswith("ord_"):
                mapping = {"ord_cry": "ord_cry.wav", "ord_err": "ord_err.wav", "ord_ren": "ord_ren.wav"}
                self.play_sound(mapping.get(new_map, "ord_mapchange.wav"))
        self.current_map = new_map

    # WebSocket views
    def start_websocket_monitor(self) -> None:
        threading.Thread(target=self._run_websocket, daemon=True).start()

    def _run_websocket(self) -> None:
        asyncio.run(self._websocket_handler())

    async def _websocket_handler(self):
        if not WEBSOCKETS_AVAILABLE:
            return
        uri = VIEWS_WEBSOCKET_URL
        while self.websocket_running:
            try:
                async with websockets.connect(uri) as websocket:  # type: ignore
                    self.root.after(0, self._update_views_status, "Connected")
                    while self.websocket_running:
                        try:
                            message = await asyncio.wait_for(websocket.recv(), timeout=30)
                            self._process_websocket_message(message)
                        except asyncio.TimeoutError:
                            try:
                                await websocket.ping()
                            except Exception:
                                break
                            continue
            except Exception as e:
                self.root.after(0, self._update_views_status, f"WebSocket Error: {str(e)}")
                await asyncio.sleep(5)

    def _process_websocket_message(self, message: str) -> None:
        try:
            data = json.loads(message)
            if data.get("type") == "NEW_VIEW":
                view_data = data["data"]
                view_id = view_data["id"]
                timestamp = view_data["timestamp"]
                dt = datetime.fromtimestamp(timestamp)
                time_str = dt.strftime("%Y-%m-%d %I:%M:%S %p")
                self.root.after(0, self._update_views_display, view_id, time_str)
                if self.last_view_id is None or int(view_id) > int(self.last_view_id):
                    self.root.after(0, self._show_new_view_notification, view_id, time_str)
                    self.root.after(0, self._log_new_view, view_id, timestamp)
                    self.root.after(0, self.play_sound, "new_view.wav")
                    self.last_view_id = view_id
        except Exception as e:
            self.root.after(0, self._update_views_status, f"Error processing message: {str(e)}")

    def _update_views_display(self, view_id, timestamp) -> None:
        self.views_label.config(text=f"Current View ID: {view_id}")
        self.last_view_time_label.config(text=f"Last View Time: {timestamp}")
        self._update_views_status("New view received")

    def _update_views_status(self, message: str) -> None:
        self.views_status.config(text=f"Status: {message}")

    def _show_new_view_notification(self, view_id, time_str) -> None:
        pass

    def _log_new_view(self, view_id, timestamp) -> None:
        try:
            with open(VIEWS_CSV_FILENAME, "a", newline="", encoding="utf-8") as f:
                utc_timestamp = datetime.now(timezone.utc).isoformat()
                csv.writer(f).writerow([utc_timestamp, view_id, timestamp])
        except Exception as e:
            print(f"Error logging view to CSV: {e}")

    # Actions
    def connect_to_cge(self) -> None:
        self.play_sound("join.wav")
        self._launch_tf2_with_connect("connect 169.150.249.133:22912")

    def connect_to_sourceTV(self) -> None:
        self.play_sound("join.wav")
        self._launch_tf2_with_connect("connect 169.150.249.133:22913")

    def _launch_tf2_with_connect(self, connect_command: str) -> None:
        try:
            server = connect_command.split(" ")[1]
            if os.name == "nt":
                steam_path = self._find_steam_executable()
                if steam_path:
                    subprocess.Popen([steam_path, "-applaunch", "440", f"+connect {server}"])
                else:
                    self.status_var.set("Steam not found. Please ensure Steam is installed.")
                    self._show_tf2_not_installed()
            else:
                subprocess.Popen(["steam", "-applaunch", "440", f"+connect {server}"])
        except Exception as e:
            self.status_var.set(f"Error launching TF2: {str(e)}")

    def _find_steam_executable(self):
        possible = [
            os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Steam", "Steam.exe"),
            os.path.join(os.environ.get("ProgramFiles", ""), "Steam", "Steam.exe"),
            os.path.join(os.environ.get("ProgramW6432", ""), "Steam", "Steam.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\\Steam\\Steam.exe"),
            os.path.expandvars(r"%USERPROFILE%\\Steam\\Steam.exe"),
        ]
        for p in possible:
            if p and os.path.exists(p):
                return p
        return None

    def _show_tf2_not_installed(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("TF2 is NOT installed")
        win.configure(bg="#2d2d2d")
        win.overrideredirect(True)
        center_window(win, 400, 200)
        tk.Label(win, text="TF2 is NOT installed", font=("Arial", 18, "bold"), bg="#2d2d2d", fg="#ff5555").pack(pady=(40, 10))
        tk.Button(win, text="Close", font=("Arial", 12), bg="#232323", fg="#ffffff", bd=0, relief=tk.FLAT,
                  activebackground="#3d3d3d", activeforeground="#ff5555", command=win.destroy, cursor="hand2").pack(pady=(10, 20))
        win.lift()
        win.attributes("-topmost", True)
        win.after(3000, win.destroy)

    # Misc
    def update_ordinance_time(self) -> None:
        now = datetime.now(timezone.utc)
        delta = now - ORDINANCE_START
        days = delta.days
        hours, rem = divmod(delta.seconds, 3600)
        minutes, seconds = divmod(rem, 60)
        self.ordinance_var.set(f"Time since start of cge7-193: {days} days, {hours:02d}:{minutes:02d}:{seconds:02d}")
        if self.running:
            self.root.after(1000, self.update_ordinance_time)

    def on_close(self) -> None:
        self.play_sound("close.wav")
        if PYGAME_AVAILABLE:
            start = time.time()
            while pygame.mixer.get_busy() and time.time() - start < 1:
                self.root.update()
                time.sleep(0.05)
        self.running = False
        self.websocket_running = False
        self.root.destroy()


def show_thank_you() -> None:
    splash = tk.Tk()
    splash.title("Welcome to Reployer")
    splash.configure(bg="#1e1e1e")
    splash.overrideredirect(True)
    try:
        icon_path = os.path.join("resources", "sourceclown.ico")
        if os.path.exists(icon_path):
            splash.iconbitmap(icon_path)
        splash.attributes("-topmost", True)
    except Exception:
        pass

    try:
        if PYGAME_AVAILABLE:
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

    try:
        from tkinter import PhotoImage
        img_frame = tk.Frame(splash, bg="#1e1e1e")
        img_frame.pack(side=tk.TOP, pady=(10, 0))
        gaq9_path = os.path.join("resources", "gaq9.png")
        sourceclown_path = os.path.join("resources", "sourceclown.png")

        if os.path.exists(gaq9_path):
            splash.gaq9_img = PhotoImage(file=gaq9_path)
            tk.Label(img_frame, image=splash.gaq9_img, bg="#1e1e1e").pack(side=tk.LEFT, padx=(0, 10))

        if os.path.exists(sourceclown_path):
            try:
                from PIL import Image as PILImage, ImageTk as PILImageTk  # type: ignore
                if os.path.exists(gaq9_path):
                    gaq9_pil = PILImage.open(gaq9_path)
                    sc_pil = PILImage.open(sourceclown_path).resize(gaq9_pil.size, PILImage.LANCZOS)
                    splash.sourceclown_img = PILImageTk.PhotoImage(sc_pil)
                else:
                    splash.sourceclown_img = PhotoImage(file=sourceclown_path)
            except Exception:
                splash.sourceclown_img = PhotoImage(file=sourceclown_path)
            tk.Label(img_frame, image=splash.sourceclown_img, bg="#1e1e1e").pack(side=tk.LEFT)
    except Exception:
        pass

    tk.Label(splash, text="Thank you for downloading Reployer!", font=("Arial", 16, "bold"),
             bg="#1e1e1e", fg="#4fc3f7").pack(pady=(5, 0))
    tk.Label(splash, text="Made by Kiverix (the clown)", font=("Arial", 12), bg="#1e1e1e", fg="#ffffff").pack(pady=(5, 0))

    loading_var = tk.StringVar(value="Loading")
    tk.Label(splash, textvariable=loading_var, font=("Arial", 14), bg="#1e1e1e", fg="#ffffff").pack(pady=10)

    center_window(splash, 500, 375)

    def animate(count=0):
        dots = "." * ((count % 4) + 1)
        loading_var.set(f"Loading{dots}")
        if splash.winfo_exists():
            splash.after(200, animate, count + 1)

    animate()
    splash.after(5000, splash.destroy)
    splash.mainloop()


if __name__ == "__main__":
    show_thank_you()
    temp_root = tk.Tk()
    temp_root.withdraw()
    try:
        default_ip = f"{CGE7_193[0]}:{CGE7_193[1]}"
    except Exception:
        default_ip = "169.150.249.133:22912"
    ip_input = simpledialog.askstring("Server address","Enter server IP (ip:port):",initialvalue=default_ip,parent=temp_root)
    temp_root.destroy()
    if not ip_input:
        sys.exit(0)
    host, sep, port_str = ip_input.strip().partition(":")
    if not host:
        host = CGE7_193[0]
    try:
        port = int(port_str) if sep and port_str.isdigit() else CGE7_193[1]
    except Exception:
        port = CGE7_193[1]
    CGE7_193 = (host, port)
    root = tk.Tk()
    try:
        icon_path = os.path.join("resources", "sourceclown.ico")
        if os.path.exists(icon_path):
            root.iconbitmap(icon_path)
    except Exception:
        pass
    center_window(root, 1500, 1000)
    app = ServerMonitorApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()