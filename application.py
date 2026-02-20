from dataclasses import dataclass, asdict, field
from typing import Optional, Dict, List, Tuple, Any
from sound import SoundSettings, SoundEngine

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