from app.rules.coc.preset import build_system
from app.rules.kernel import register_system

register_system(build_system())
