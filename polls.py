from dataclasses import dataclass
from typing import List, Dict

@dataclass
class PollResult:
    ok: bool
    server_name: str
    map_name: str
    player_count: int
    max_players: int
    players: List[Dict[str, object]]  # {name, score, duration}
    error: str = ""