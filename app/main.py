from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os

from app.config import settings
from app.routers import auth, authorizations, ws, sedes, admin_users
from app.websocket import manager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create upload directory if it doesn't exist
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    # Start Redis WebSocket listener
    await manager.start_redis_listener()
    yield
    # Close Redis client/tasks on shutdown
    await manager.close()

app = FastAPI(
    title="CB Authorizations API",
    version="1.0.0",
    lifespan=lifespan
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In production, restrict this to the frontend domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount uploads directory to serve files
# StaticFiles will fail if the directory does not exist on startup,
# but our lifespan context manager makes it before it's loaded, or we make it here
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")
# Include routers
app.include_router(auth.router)
app.include_router(authorizations.router)
app.include_router(ws.router)
app.include_router(sedes.router)
app.include_router(admin_users.router)

@app.get("/")
def read_root():
    return {"message": "CB Authorizations API is running"}
