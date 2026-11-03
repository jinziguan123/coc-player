from fastapi import APIRouter

from app.api.agent_settings import router as agent_settings_router
from app.api.embedding_models import router as embedding_models_router
from app.api.model_source import router as model_source_router
from app.api.reranker_settings import router as reranker_settings_router
from app.api.tasks import router as tasks_router
from app.api.ai_settings import public_router as ai_settings_public_router
from app.api.ai_settings import router as ai_settings_router
from app.api.bgm import router as bgm_router
from app.api.characters import router as characters_router
from app.api.chat import router as chat_router
from app.api.combat import router as combat_router
from app.api.inventory import router as inventory_router
from app.api.kp import router as kp_router
from app.api.images import router as images_router
from app.api.modules import router as modules_router
from app.api.net import router as net_router
from app.api.onboarding import router as onboarding_router
from app.api.rulebooks import router as rulebooks_router
from app.api.sessions import router as sessions_router

api_router = APIRouter()
api_router.include_router(images_router)
api_router.include_router(bgm_router)
api_router.include_router(modules_router)
api_router.include_router(onboarding_router)
api_router.include_router(characters_router)
api_router.include_router(sessions_router)
api_router.include_router(chat_router)
api_router.include_router(kp_router)
api_router.include_router(combat_router)
api_router.include_router(inventory_router)
api_router.include_router(ai_settings_router)
api_router.include_router(ai_settings_public_router)
api_router.include_router(agent_settings_router)
api_router.include_router(embedding_models_router)
api_router.include_router(reranker_settings_router)
api_router.include_router(model_source_router)
api_router.include_router(tasks_router)
api_router.include_router(rulebooks_router)
api_router.include_router(net_router)
