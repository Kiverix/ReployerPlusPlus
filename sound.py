from dataclasses import dataclass, asdict, field
from typing import Optional, Dict, List, Tuple, Any, TYPE_CHECKING
import os

if TYPE_CHECKING:
    from application import AppPrefs
    
try:
    import pygame
    pygame.mixer.init()
    pygame.mixer.set_num_channels(16)
    PYGAME_AVAILABLE = True
except Exception:
    PYGAME_AVAILABLE = False


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