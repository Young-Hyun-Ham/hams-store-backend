#  app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os

from app.routers.menu import router as menu_router
from app.routers.orders import router as orders_router
from app.routers.admin_orders import router as admin_orders_router
from app.routers.devices import router as devices_router
from app.routers.admin_notifications import router as admin_notifications_router

app = FastAPI(title="임진매운갈비 API")

ALLOW_ORIGINS = os.getenv("ALLOW_ORIGINS", "")
origins = [o.strip() for o in ALLOW_ORIGINS.split(",") if o.strip()]

# ALLOW_ORIGINS 비어있으면 로컬 기본값
if not origins:
  origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
  ]

app.add_middleware(
  CORSMiddleware,
  allow_origins=origins,          # 특정 origin만
  allow_credentials=True,         # 쿠키/인증 필요 시
  allow_methods=["*"],            # GET/POST/PUT/DELETE/OPTIONS 전부
  allow_headers=["*"],            # Authorization, Content-Type 등 전부
  expose_headers=["*"],           # 필요하면 유지
)


@app.get("/health")
def health():
    return {"status": "ok"}

app.include_router(menu_router)
app.include_router(orders_router)
app.include_router(admin_orders_router)
app.include_router(devices_router)
app.include_router(admin_notifications_router)
