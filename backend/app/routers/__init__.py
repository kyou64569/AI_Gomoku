from .configs import router as configs_router
from .players import router as players_router
from .rooms import router as rooms_router
from .game import router as game_router
from .history import router as history_router

__all__ = ["configs_router", "players_router", "rooms_router", "game_router", "history_router"]
