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
    PYGAME = pygame
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
