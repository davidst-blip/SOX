from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.routers import auth, controls, knowledge, users

app = FastAPI(
    title="SOX Sentinel",
    description="Internal SOX documentation review and gap-analysis platform — Perion Network Ltd.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(controls.router)
app.include_router(knowledge.router)


@app.get("/")
async def root():
    return {"name": "SOX Sentinel", "version": "0.1.0"}


@app.get("/health")
async def health():
    return {"status": "ok"}
