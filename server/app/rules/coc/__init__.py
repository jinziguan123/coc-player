from app.rules.coc.engine import CoCRuleEngine
from app.rules.coc.preset import build_system
from app.rules.kernel import register_system
from app.rules.registry import register_engine

_engine = CoCRuleEngine()
register_engine(_engine)
register_system(build_system(_engine))
