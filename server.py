from typing import Optional, Dict, Tuple, Any, Tuple
from dataclasses import dataclass
from utils import parse_address, normalize_fastdl

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
