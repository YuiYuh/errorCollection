import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from .config import settings
from .db import init_db
from .services.worker import TaskWorker
from .routers import tasks, questions, reviews, system, stats
from .routers import learning

worker = TaskWorker()

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs(); init_db(); await worker.start()
    yield
    await worker.stop()

app = FastAPI(title="WrongBook", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(tasks.router); app.include_router(questions.router); app.include_router(reviews.router); app.include_router(system.router)
app.include_router(stats.router)
app.include_router(learning.router)

@app.middleware('http')
async def revalidate_page(request, call_next):
    response = await call_next(request)
    # Hashed JS/CSS can be cached; the entry page must discover the latest build.
    if response.headers.get('content-type', '').startswith('text/html'):
        response.headers['Cache-Control'] = 'no-cache, max-age=0, must-revalidate'
    return response

@app.get("/api/health")
def health(): return {"status": "ok"}

dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if dist.exists(): app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
