# -*- coding: utf-8 -*-
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import logging
import os
from fastapi import FastAPI, Request, Depends
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from api.auth import require_auth
from api.config import config
from api.routers import admin
from api.routers import camera
from api.routers import debate
from api.logs import configure_logging
from api.version import __version__
from debate.services.session_store import session_store

if config.runtime_mode == "cloud":
    from api.routers import generate
    from api.routers import jailbreak_reports
    from api.routers import prompts
    from api.routers import vip

# Get logger for this module
logger = logging.getLogger(f"lia.{__name__}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown logic"""

    configure_logging()

    # Startup
    logger.info(f"Starting debate API service (version: {__version__})")

    # Create debate history directory
    history_dir = os.path.expanduser("~/debate_histories")
    os.makedirs(history_dir, exist_ok=True)
    logger.info(f"Debate history directory: {history_dir}")

    # Start session cleanup task
    session_store.start_cleanup_task()

    if config.runtime_mode == "cloud":
        from debate.services.shared import warmup_knowledge_base_service

        logger.info("Warming up Knowledge Base service...")
        await warmup_knowledge_base_service()
    else:
        logger.info("Deterministic mode: external providers are disabled")

    yield

    # Shutdown
    logger.info("Shutting down debate API service")

    await session_store.stop_cleanup_task()
    await session_store.cleanup_all_sessions()
    if config.runtime_mode == "cloud":
        from debate.services.shared import shutdown_shared_services

        await shutdown_shared_services()


app = FastAPI(
    title="Debate API",
    description="""
    Multi-agent debate moderation system.
    
    This API provides a WebSocket-based interface to run moderated debates with AI agents.
    
    ## Features
    
    - Start debate sessions with multiple participants
    - Real-time WebSocket communication
    - AI-powered moderation and questioning
    - Debate history and summaries
    
    ## Quick Start
    
    1. Start a debate session via POST /api/debate/start
    2. Connect to the WebSocket URL provided
    3. Receive prompts and send user inputs
    4. Retrieve history and summaries when complete
    
    View interactive API documentation at `/docs` or ReDoc at `/redoc`.
    """,
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logger.info(
    "CORS configured: env=%s allowed_origins=%s",
    config.app_env,
    config.allowed_origins,
)


@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    """Log all requests"""
    logger.info(f"{request.method} {request.url.path}")
    response = await call_next(request)
    return response


# Include routers
app.include_router(admin.router)
app.include_router(camera.router)
app.include_router(debate.router)
if config.runtime_mode == "cloud":
    app.include_router(generate.router)
    app.include_router(jailbreak_reports.router)
    app.include_router(prompts.router)
    app.include_router(vip.router)


@app.get("/healthz")
async def healthz():
    return {
        "status": "ok",
        "runtime_mode": config.runtime_mode,
        "providers": config.provider_status,
    }

# Mount asyncapi directory for proper asset loading
# Static files are at project root, go up from src/api/main.py
from pathlib import Path
_static_base = Path(__file__).parent.parent.parent / "static"
try:
    asyncapi_static = StaticFiles(directory=str(_static_base / "asyncapi"), html=True)
    app.mount("/asyncapi", asyncapi_static, name="asyncapi")
except Exception as e:
    logger.warning(f"Could not mount asyncapi static files: {e}")


@app.get("/asyncapi.yaml")
async def get_asyncapi_spec():
    """Serve AsyncAPI specification for WebSocket interface"""
    return FileResponse(
        path=str(_static_base / "asyncapi.yaml"),
        media_type="application/yaml"
    )

@app.get("/test")
async def test_client():
    """Serve the WebSocket test client"""
    return FileResponse(
        path=str(_static_base / "test.html"),
        media_type="text/html"
    )

@app.get("/camera")
async def camera_monitor():
    """Camera LLM monitor — auto-connects to camera session"""
    return FileResponse(
        path=str(_static_base / "camera.html"),
        media_type="text/html"
    )


@app.get("/camera-welcome-test")
async def camera_welcome_test():
    """Camera → Welcome transition test page"""
    return FileResponse(
        path=str(_static_base / "camera_welcome_test.html"),
        media_type="text/html"
    )


@app.get("/pledges/test")
async def pledges_test_client():
    """Serve the pledges test client"""
    return FileResponse(
        path=str(_static_base / "pledges_test.html"),
        media_type="text/html"
    )

@app.get("/", response_class=HTMLResponse)
async def root(user: dict = Depends(require_auth())):
    """Landing page with API documentation links"""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Debate API</title>
        <style>
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                max-width: 800px;
                margin: 50px auto;
                padding: 20px;
                line-height: 1.6;
                color: #333;
            }
            h1 {
                color: #1a1a1a;
                border-bottom: 3px solid #00a4e4;
                padding-bottom: 10px;
            }
            .card {
                background: #f5f5f5;
                border-radius: 8px;
                padding: 20px;
                margin: 20px 0;
            }
            .link-button {
                display: inline-block;
                background: #00a4e4;
                color: white;
                padding: 12px 24px;
                border-radius: 6px;
                text-decoration: none;
                font-weight: 600;
                margin: 10px 10px 10px 0;
            }
            .link-button:hover {
                background: #0078a4;
            }
            .primary {
                background: #28a745;
                font-size: 18px;
                padding: 16px 32px;
                box-shadow: 0 4px 8px rgba(40, 167, 69, 0.3);
            }
            .primary:hover {
                background: #218838;
                transform: translateY(-2px);
                box-shadow: 0 6px 12px rgba(40, 167, 69, 0.4);
            }
            .secondary {
                background: #6c757d;
            }
            .secondary:hover {
                background: #5a6268;
            }
            ul {
                margin: 10px 0;
                padding-left: 30px;
            }
        </style>
    </head>
    <body>
        <h1>🏛️ Debate API</h1>
        
        <div class="card">
            <h2>Welcome</h2>
            <p>Multi-agent debate moderation system for facilitating structured discussions.</p>
        </div>
        
        <div class="card">
            <h2>🚀 Quick Start</h2>
            <p>Test the WebSocket interface with our interactive client:</p>
            <a href="/test" class="link-button primary">🧪 WebSocket Test Client</a>
        </div>

        <div class="card">
            <h2>🧭 Pledges Generator</h2>
            <p>Try the pledge generation UI with input pledges and less/default/more options:</p>
            <a href="/pledges/test" class="link-button primary">✨ Pledges Test Client</a>
        </div>
        
        <div class="card">
            <h2>📚 API Documentation</h2>
            <p>Explore the API with our interactive documentation:</p>
            
            <a href="/docs" class="link-button">📖 REST API (Swagger)</a>
            <a href="/redoc" class="link-button secondary">📋 REST API (ReDoc)</a>
            <a href="/asyncapi" class="link-button">🔌 WebSocket Docs (Interactive)</a>
            <a href="/asyncapi.yaml" class="link-button secondary">📄 WebSocket Spec (YAML)</a>
        </div>
        
        <div class="card">
            <h2>Endpoints</h2>
            <ul>
                <li><strong>POST /api/debate/start</strong> - Start a new debate session</li>
                <li><strong>GET /api/debate/history/{session_id}</strong> - Get debate history</li>
                <li><strong>WebSocket /api/debate/ws/{session_id}</strong> - Join session</li>
                <li><strong>POST /api/generate/pledges</strong> - Generate pledge options</li>
            </ul>
        </div>
        
        <div class="card">
            <h2>WebSocket Interface</h2>
            <p>The debate system uses WebSockets for real-time communication. Connect to:</p>
            <code>/api/debate/ws/{session_id}</code>
            <p>View the complete AsyncAPI specification for detailed message schemas and examples.</p>
        </div>
        
        <div class="card">
            <h2>Features</h2>
            <ul>
                <li>Real-time WebSocket communication</li>
                <li>AI-powered moderation and questioning</li>
                <li>Structured debate flow with rounds</li>
                <li>Debate history and summaries</li>
            </ul>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)
