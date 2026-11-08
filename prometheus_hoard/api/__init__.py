from .agent import router as agent_router
from .desktop import router as desktop_router
from .ui import router as ui_router

ROUTERS = [agent_router, desktop_router, ui_router]
