"""
Research Assistant API
FastAPI application for research assistant with RAG backend integration
"""
import sys
import os
import logging
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from fastapi import FastAPI

from config import (
    API_TITLE, API_DESCRIPTION, API_VERSION, AGENT_INSTRUCTIONS, AGENT_DEEP_RESEARCH_INSTRUCTIONS,
    GSF_INSTRUCTIONS, GSF_DEEP_RESEARCH_INSTRUCTIONS,
    MODEL_PROVIDER, AGENT_OPENAI_MODEL_ID, GEMINI_MODEL_ID, VERTEX_MODEL_ID,
    MODEL_PROVIDER_DISPLAY, IE_EMBEDDING_MODEL,
)
from api.routes.auth import AuthRouter
from api.routes.tools import ToolsRouter
from api.routes.collections import CollectionsRouter
from api.routes.sessions import SessionsRouter
from api.routes.prompts import PromptsRouter

# Configure logging
logging.basicConfig(level=logging.INFO)
logging.getLogger("agno").setLevel(logging.DEBUG)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title=API_TITLE,
    description=API_DESCRIPTION,
    version=API_VERSION
)

# Register routes
app.post("/api/v1/login", response_model=None)(AuthRouter.login)
app.get("/api/auth/login/nvlogin")(AuthRouter.azure_ad_login)
app.get("/api/auth/callback/nvlogin")(AuthRouter.azure_ad_callback)
app.get("/api/v1/auth/exchange-session")(AuthRouter.exchange_session_token)
app.get("/api/v1/tools", response_model=None)(ToolsRouter.get_tools)
app.get("/api/v1/collections", response_model=None)(CollectionsRouter.get_collections)
app.get("/api/v1/fullcollections", response_model=None)(CollectionsRouter.get_full_collections)
app.post("/api/v1/collections", response_model=None)(CollectionsRouter.create_collection_endpoint)
app.delete("/api/v1/collections", response_model=None)(CollectionsRouter.delete_collections_endpoint)
app.post("/api/v1/documents")(CollectionsRouter.upload_document_endpoint)
app.get("/api/v1/documents", response_model=None)(CollectionsRouter.list_documents_endpoint)
app.post("/api/v1/prompt", response_model=None)(PromptsRouter.send_prompt)
app.post("/api/v1/prompt/stream")(PromptsRouter.send_prompt_stream)
app.get("/api/v1/sessions", response_model=None)(SessionsRouter.list_sessions)
app.get("/api/v1/sessions/{session_id}", response_model=None)(SessionsRouter.get_session)
app.patch("/api/v1/sessions/{session_id}", response_model=None)(SessionsRouter.update_session)

@app.get("/api/v1/system-prompt")
async def get_system_prompt():
    """Get system prompts for the agent"""
    return {
        "system_prompt": AGENT_INSTRUCTIONS,
        "deep_research_prompt": AGENT_DEEP_RESEARCH_INSTRUCTIONS,
        "gsf_prompt": GSF_INSTRUCTIONS,
        "gsf_deep_research_prompt": GSF_DEEP_RESEARCH_INSTRUCTIONS,
    }

_PROVIDER_LABELS = {"nvidia": "NVIDIA", "gemini": "Gemini", "vertex": "Vertex AI"}

@app.get("/api/v1/model-info")
async def get_model_info():
    """Get current model provider and model ID.

    MODEL_PROVIDER is the routing key (e.g. nvidia → OpenAIChat).
    MODEL_PROVIDER_DISPLAY overrides the GUI badge (e.g. Microsoft Foundry).
    """
    if MODEL_PROVIDER == "gemini":
        model_id = GEMINI_MODEL_ID
    elif MODEL_PROVIDER == "vertex":
        model_id = VERTEX_MODEL_ID
    else:
        model_id = AGENT_OPENAI_MODEL_ID
    provider_label = MODEL_PROVIDER_DISPLAY or _PROVIDER_LABELS.get(MODEL_PROVIDER, MODEL_PROVIDER)
    return {"provider": provider_label, "model_id": model_id, "embedding_model": IE_EMBEDDING_MODEL}

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}
